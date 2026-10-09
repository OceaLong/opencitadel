"""Fresh metadata validation. A saved preflight is evidence, never admission authority."""

from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import Field

from app.domain.evaluation.configuration import (
    ConfigVersion,
    SuiteVersion,
    dataset_membership_digest,
    digest,
    validate_matrix,
)
from app.domain.evaluation.dataset import ImmutableModel
from app.domain.evaluation.errors import DatasetNotFound, DatasetUnavailable
from app.domain.evaluation.rubric import RubricVersion
from app.domain.models.resource_pin import ResourceUnavailable
from app.domain.runtime_policy import RuntimePolicyPair


def validate_current_config(version, current):
    comparisons = {
        "effective_policy": "policy_changed",
        "identity": "model_changed",
        "contract_digest": "contract_changed",
        "prompt_digest": "prompt_changed",
        "skill_digest": "skill_changed",
        "settings": "inference_settings_changed",
        "capabilities": "model_capabilities_changed",
        "budget": "budget_candidates_changed",
    }
    return tuple(
        reason
        for field, reason in comparisons.items()
        if version.snapshot.get(field) != current.get(field)
    )


class DependencyEvidence(ImmutableModel):
    ready: bool = False
    revision: str | None = None
    errors: tuple[str, ...] = ()
    price_coverage: Literal["unknown", "partial", "complete"] = "unknown"
    physical_call_upper_bound: int | None = Field(default=None, ge=0)


class PreflightAuthority(Protocol):
    async def check(
        self, scope, suite: SuiteVersion, configs: tuple[ConfigVersion, ...], *, for_start: bool
    ) -> DependencyEvidence:
        """Read only current scoped E03/E04/E05 authority. Never allocate or contact targets."""
        ...


class UnavailableAuthority:
    def __init__(self, code):
        self.code = code

    async def check(self, scope, suite, configs, *, for_start):
        return DependencyEvidence(errors=(self.code,))


class PreflightResult(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    suite_version: UUID
    revision: int = 0
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    quantity: int
    physical_call_upper_bound: int | None = None
    price_coverage: Literal["unknown", "partial", "complete"]
    token_budget: int
    environment_ready: bool
    allowed: bool
    evidence: dict[str, str]


class PreflightService:
    def __init__(self, suites, principal, *, recordings=None, environments=None, budgets=None):
        self.suites, self.principal = suites, principal
        self.recordings = recordings or UnavailableAuthority("recording_authority_unavailable")
        self.environments = environments or UnavailableAuthority(
            "environment_authority_unavailable"
        )
        self.budgets = (
            budgets
            or getattr(suites, "budgets", None)
            or UnavailableAuthority("budget_authority_unavailable")
        )

    async def check(self, scope, suite_id):
        policy_pair = await self.suites.policies.load_active_pair()
        async with self.suites.uow_factory(self.suites.auth(scope, self.principal)) as uow:
            result = await self._current(
                uow, scope, suite_id, for_start=False, policy_pair=policy_pair
            )
            # Persistence is the only preflight side effect; no audit-privileged path.
            await uow.evaluation_dataset.authorize(scope, self.principal, write=True)
            result = await uow.evaluation_configuration.save_preflight(scope, result)
            await uow.evaluation_dataset.authorize(scope, self.principal, write=True)
            await uow.commit()
            return result

    async def revalidate_for_start(
        self, scope, suite_id, *, uow=None, policy_pair: RuntimePolicyPair | None = None
    ):
        """E06 supplies its actual admission pair when borrowing a transaction.

        No fallback checkout inside a caller UoW. Returned checked revisions must
        match the pair actually used by admission; old preflight never grants access.
        """
        if uow is not None:
            if policy_pair is None:
                raise ValueError("actual_policy_pair_required")
            return await self._current(
                uow, scope, suite_id, for_start=True, policy_pair=policy_pair
            )
        current_pair = await self.suites.policies.load_active_pair()
        async with self.suites.uow_factory(self.suites.auth(scope, self.principal)) as owned:
            return await self._current(
                owned, scope, suite_id, for_start=True, policy_pair=current_pair
            )

    async def _current(self, uow, scope, suite_id, *, for_start, policy_pair: RuntimePolicyPair):
        await uow.evaluation_dataset.authorize(scope, self.principal, write=True)
        repo = uow.evaluation_configuration
        suite = SuiteVersion.model_validate(await repo.get_version(scope, "suite", suite_id))
        errors, warnings, configs = [], [], []
        evidence = {
            "suite": str(suite.id),
            "suite_fingerprint": suite.fingerprint,
            "authorization": digest(self.principal.model_dump(mode="json")),
            "deployment": digest(self.suites.limits().model_dump(mode="json")),
        }
        try:
            suite.settings.validate_limits(self.suites.limits())
        except ValueError:
            errors.append("deployment_limit_exceeded")
        try:
            dataset = await uow.evaluation_dataset.get_version(scope, suite.dataset_version)
            proof = suite.dataset_proof
            if (
                dataset["revision"] != proof.revision
                or dataset_membership_digest(dataset) != proof.membership_digest
            ):
                raise ValueError("dataset_metadata_changed")
            pins = await uow.resource_pins.validate(
                scope, "dataset_version", str(suite.dataset_version), proof.resources
            )
            if any(not pin.available for pin in pins):
                raise ResourceUnavailable("dataset_resource_unavailable")
            rubric = RubricVersion.model_validate(
                await repo.get_version(scope, "rubric", suite.rubric_version)
            )
            quantity = validate_matrix(
                len(dataset["members"]), len(suite.config_versions), suite.settings.repeat
            )
            if quantity != suite.quantity or quantity > suite.settings.max_results:
                errors.append("matrix_changed")
            evidence.update(
                dataset=str(suite.dataset_version),
                dataset_revision=str(dataset["revision"]),
                rubric=str(rubric.id),
            )
        except (DatasetUnavailable, DatasetNotFound, ResourceUnavailable, ValueError):
            errors.append("dataset_or_rubric_unavailable")
            rubric = None
        identities = (*suite.config_versions, *((rubric.judge_config_version,) if rubric else ()))
        for identity in identities:
            try:
                version = ConfigVersion.model_validate(
                    await repo.get_version(scope, "config", identity)
                )
                current = await self.suites.resolved(
                    uow, scope, version.selection, self.principal, policy_pair=policy_pair
                )
                errors.extend(validate_current_config(version, current))
                if (
                    not current["credential_configured"]
                    and current["identity"]["provider"] != "ollama"
                ):
                    errors.append("credential_unavailable")
                pins = await uow.resource_pins.validate(
                    scope, "config_version", str(version.id), version.selection.resources
                )
                if any(not pin.available for pin in pins):
                    errors.append("resource_unavailable")
                if version.version_unpinned:
                    warnings.append("version_unpinned")
                if identity == (rubric.judge_config_version if rubric else None) and (
                    version.selection.purpose != "evaluation_judge" or version.selection.tool_names
                ):
                    errors.append("independent_judge_required")
                configs.append(version)
                evidence["policy"] = current["policy_revision"]
                evidence["operations"] = current["operations_revision"]
                evidence["config:" + str(version.id)] = version.fingerprint
            except (DatasetNotFound, ResourceUnavailable, ValueError):
                errors.append("configuration_unavailable")
        authority = self.recordings if suite.mode == "recorded" else self.environments
        borrowed_check = getattr(authority, "check_in_uow", None)
        metadata = (
            await borrowed_check(
                scope, suite, tuple(configs), for_start=for_start, uow=uow, principal=self.principal
            )
            if borrowed_check
            else await authority.check(scope, suite, tuple(configs), for_start=for_start)
        )
        budget_check = getattr(self.budgets, "check_in_uow", None)
        budget = (
            await budget_check(
                scope, suite, tuple(configs), for_start=for_start, uow=uow, principal=self.principal
            )
            if budget_check
            else await self.budgets.check(scope, suite, tuple(configs), for_start=for_start)
        )
        errors.extend(metadata.errors)
        errors.extend(budget.errors)
        if not metadata.ready and not metadata.errors:
            errors.append("execution_dependency_unavailable")
        if not budget.ready and not budget.errors:
            errors.append("budget_unavailable")
        if suite.settings.money_budget is not None and budget.price_coverage != "complete":
            errors.append("price_coverage_incomplete")
        if metadata.revision:
            evidence["execution_dependency"] = metadata.revision
        if budget.revision:
            evidence["budget"] = budget.revision
        await uow.evaluation_dataset.authorize(scope, self.principal, write=True)
        return PreflightResult(
            suite_version=suite.id,
            errors=tuple(sorted(set(errors))),
            warnings=tuple(sorted(set(warnings))),
            quantity=suite.quantity,
            physical_call_upper_bound=budget.physical_call_upper_bound,
            price_coverage=budget.price_coverage,
            token_budget=suite.settings.token_budget,
            environment_ready=metadata.ready if suite.mode == "isolated" else False,
            allowed=not errors,
            evidence=evidence,
        )
