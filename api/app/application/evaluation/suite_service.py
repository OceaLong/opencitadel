"""Real version publication with current authority, fixed dependencies and atomic audit."""

import base64
import hashlib
import hmac
import json
from uuid import UUID, uuid4

from app.application.evaluation.configuration_metadata import (
    UnavailableExternalContracts,
    builtin_contracts,
    contract_fingerprints,
)
from app.application.evaluation.dataset_service import fingerprint
from app.application.execution.content_sanitization import sanitize_content
from app.application.execution.system_prompt import platform_system_prompt
from app.domain.evaluation.configuration import (
    ConfigSelection,
    ConfigVersion,
    DatasetProof,
    SuiteDefinition,
    SuiteVersion,
    dataset_membership_digest,
    digest,
    validate_matrix,
)
from app.domain.evaluation.dataset import ImmutableModel
from app.domain.evaluation.errors import DatasetConflict
from app.domain.evaluation.rubric import RubricDefinition, RubricVersion, validate_references
from app.domain.models.audit_log import AuditLog
from app.domain.models.authorization import AuthorizationContext
from app.domain.runtime_policy import RuntimePolicyPair
from app.domain.services.skills.skill_loader import render_active

DEFINITIONS = {"config": ConfigSelection, "rubric": RubricDefinition, "suite": SuiteDefinition}
VERSIONS = {"config": ConfigVersion, "rubric": RubricVersion, "suite": SuiteVersion}


class ConfigurationDraft(ImmutableModel):
    id: UUID
    kind: str
    name: str
    revision: int
    definition: dict


class ConfigurationSummary(ImmutableModel):
    id: UUID
    name: str
    revision: int


class ConfigurationPage(ImmutableModel):
    items: tuple[ConfigurationSummary, ...]
    next_cursor: str | None = None


class SuiteService:
    def __init__(
        self,
        uow_factory,
        datasets,
        *,
        limits,
        policies,
        cursor_secret,
        external_contracts=None,
        external_contracts_factory=None,
        budgets=None,
    ):
        self.uow_factory, self.datasets, self.limits = uow_factory, datasets, limits
        self.external_contracts = external_contracts or UnavailableExternalContracts()
        self.external_contracts_factory = external_contracts_factory
        self.budgets = budgets
        self.policies = policies
        self.cursor_secret = cursor_secret
        if len(cursor_secret) < 16:
            raise ValueError("cursor secret too short")

    async def builtin_choices(self, scope, principal, *, mode="agent", skill_id=None):
        import inspect

        from app.application.evaluation.configuration_metadata import BUILTINS
        from app.application.evaluation.discovery import BuiltinToolChoice

        if mode not in {"ask", "agent"}:
            raise ValueError("invalid_mode")
        async with self.uow_factory(self.auth(scope, principal)) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=False)
            allowed = None
            if skill_id:
                skill = await uow.skill.get_by_id(skill_id, scope=scope)
                if not skill or not skill.enabled or skill.override_base_rules:
                    raise ValueError("skill_unavailable")
                allowed = skill.allowed_tools
            names = {
                getattr(method, "_tool_name", None)
                for cls in BUILTINS
                for _, method in inspect.getmembers_static(cls, inspect.isfunction)
            } - {None}
            result = []
            for name in sorted(names):
                try:
                    builtin_contracts((name,), mode=mode, allowed_tools=allowed)
                except ValueError:
                    continue
                result.append(BuiltinToolChoice(name=name, mode=mode))
            await uow.evaluation_dataset.authorize(scope, principal, write=False)
            return result

    def auth(self, scope, principal, request_id=""):
        return AuthorizationContext.for_principal(principal, scope=scope, request_id=request_id)

    async def resolved(self, uow, scope, selection, principal, *, policy_pair: RuntimePolicyPair):
        repo = uow.evaluation_configuration
        model = await repo.metadata(scope, selection)
        if model["extra_params_present"]:
            raise ValueError("unsupported_inference_parameters")
        if selection.seed is not None:
            # Existing provider adapter controls do not expose a trusted seed capability.
            raise ValueError("seed_capability_unavailable")
        settings = dict(model["settings"])
        if selection.temperature is not None:
            settings["temperature"] = selection.temperature
        if selection.max_output_tokens is not None:
            settings["max_output_tokens"] = selection.max_output_tokens
        skill = None
        if selection.skill_id:
            skill = await uow.skill.get_by_id(selection.skill_id, scope=scope)
            if not skill or not skill.enabled:
                raise ValueError("skill_unavailable")
            if skill.override_base_rules:
                raise ValueError("governance_override_forbidden")
            text = render_active(skill)
            if sanitize_content(text) != text or any(
                r.content is not None and sanitize_content(r.content) != r.content
                for r in skill.resources
            ):
                raise ValueError("sensitive_configuration_text")
            if any(r.path and r.content is None for r in skill.resources):
                raise ValueError("skill_resource_unavailable")
        builtin_names = tuple(
            name for name in selection.tool_names if not name.startswith(("mcp", "a2a"))
        )
        external_names = tuple(name for name in selection.tool_names if name not in builtin_names)
        contracts = builtin_contracts(
            builtin_names, mode=selection.mode, allowed_tools=skill.allowed_tools if skill else None
        )
        if external_names:
            if selection.external_contract_ref is None:
                raise ValueError("contract_unavailable")
            authority = (
                self.external_contracts_factory(uow)
                if self.external_contracts_factory
                else self.external_contracts
            )
            external = await authority.contracts(
                scope, principal, external_names, reference=selection.external_contract_ref
            )
            if {c.name for c in external} != set(external_names) or len(external) != len(
                external_names
            ):
                raise ValueError("contract_unavailable")
            from app.domain.models.session_mode import SessionMode
            from app.domain.services.tools.capability_policy import CapabilityPolicy

            policy = CapabilityPolicy.for_mode(
                SessionMode(selection.mode), skill.allowed_tools if skill else None
            )
            for contract in external:
                if not policy.allows_integration(contract.policy, tool_name=contract.name):
                    raise ValueError("tool_policy_denied")
                if contract.schema_body.get("function", {}).get("name") != contract.name:
                    raise ValueError("contract_unavailable")
                if skill and contract.connector_id not in (
                    skill.mcp_server_refs if contract.pack == "mcp" else skill.a2a_server_refs
                ):
                    raise ValueError("contract_unavailable")
            contracts += tuple(
                {
                    "name": c.name,
                    "pack": c.pack,
                    "schema": c.schema_body,
                    "policy": c.policy.model_dump(mode="json"),
                    "connector_id": c.connector_id,
                    "binding_revision": c.binding_revision,
                    "authority_revision": c.authority_revision,
                }
                for c in external
            )
        skill_identity = (
            {
                "id": skill.id,
                "updated_at": skill.updated_at.isoformat(),
                "allowed_tools": skill.allowed_tools,
            }
            if skill
            else None
        )
        legacy, contract = contract_fingerprints(
            contracts, mode=selection.mode, skill=skill_identity
        )
        prompt = {
            "template_revision": "model-call-system-v1",
            "governance": platform_system_prompt(
                "ask" if selection.purpose == "evaluation_judge" else selection.mode
            ),
            "skill": render_active(skill) if skill else None,
            "user_instructions": selection.prompt,
        }
        for resource in selection.resources:
            await uow.resource_pins.resolve(scope, resource)
        pair = policy_pair
        policy = {
            "policy_revision": str(pair.execution.revision.id),
            "operations_revision": str(pair.operations.revision.id),
            "effective_policy": {
                "execution": pair.execution.revision.policy.model_dump(mode="json"),
                "operations": pair.operations.revision.policy.model_dump(mode="json"),
            },
        }
        budget = (
            await self.budgets.candidates_in_uow(
                uow,
                scope,
                selection,
                model,
                policy=pair.execution.revision.policy.model_resilience,
            )
            if self.budgets
            else None
        )
        return {
            **model,
            **policy,
            **({"budget": budget} if budget is not None else {}),
            "settings": settings,
            "prompt": prompt,
            "prompt_digest": digest(prompt),
            "skill": skill.model_dump(mode="json") if skill else None,
            "skill_digest": digest(skill.model_dump(mode="json")) if skill else None,
            "contracts": list(contracts),
            "contract_digest": contract,
            "legacy_catalog_fingerprint": legacy,
            "resources": [r.model_dump(mode="json") for r in selection.resources],
        }

    async def _begin(self, uow, scope, principal, request_id, operation, payload):
        if not request_id.strip() or len(request_id) > 255:
            raise ValueError("invalid_request_id")
        await uow.evaluation_dataset.authorize(scope, principal, write=True)
        await uow.evaluation_dataset.lock_request(scope, request_id)
        value = fingerprint(operation, payload)
        return value, await uow.evaluation_dataset.receipt(scope, request_id, value)

    async def _finish(
        self, uow, scope, principal, request_id, fingerprint_value, kind, operation, result
    ):
        await uow.evaluation_dataset.authorize(scope, principal, write=True)
        entity = getattr(result, "entity_id", result.id)
        receipt = {
            "id": str(result.id),
            "revision": result.revision,
            "operation": kind + "." + operation,
            "audit_resource_id": str(entity),
        }
        if isinstance(result, ConfigurationDraft):
            receipt["draft_result"] = result.model_dump(mode="json")
        await uow.evaluation_dataset.save_receipt(scope, request_id, fingerprint_value, receipt)
        await uow.audit.add_evaluation(
            AuditLog(
                actor_user_id=principal.user_id,
                team_id=scope.team_id,
                action=f"evaluation.{kind}.{operation}",
                resource_type="evaluation_" + kind,
                resource_id=str(entity),
                request_id=request_id,
                metadata={"revision": result.revision},
            ),
            authorization=self.auth(scope, principal, request_id),
        )
        await uow.commit()
        return result

    @staticmethod
    def definition(kind, definition):
        if kind not in DEFINITIONS:
            raise ValueError("invalid_configuration_kind")
        value = DEFINITIONS[kind].model_validate(definition)
        if kind == "config" and sanitize_content(value.prompt) != value.prompt:
            raise ValueError("sensitive_configuration_text")
        return value

    async def create(self, scope, principal, *, kind, name, definition, request_id):
        definition = self.definition(kind, definition).model_dump(mode="json")
        if not name.strip() or len(name) > 255:
            raise ValueError("invalid_name")
        async with self.uow_factory(self.auth(scope, principal, request_id)) as uow:
            fp, prior = await self._begin(
                uow, scope, principal, request_id, kind + ".create", [name, definition]
            )
            if prior:
                return ConfigurationDraft.model_validate(prior["draft_result"])
            identity = uuid4()
            await uow.evaluation_configuration.create(scope, kind, identity, name, definition)
            result = ConfigurationDraft(
                id=identity, kind=kind, name=name, revision=1, definition=definition
            )
            return await self._finish(uow, scope, principal, request_id, fp, kind, "create", result)

    async def update(
        self,
        scope,
        principal,
        *,
        kind,
        entity_id,
        name,
        definition,
        expected_revision,
        request_id,
        delete=False,
    ):
        definition = self.definition(kind, definition).model_dump(mode="json")
        if not name.strip() or len(name) > 255:
            raise ValueError("invalid_name")
        operation = "delete" if delete else "update"
        async with self.uow_factory(self.auth(scope, principal, request_id)) as uow:
            fp, prior = await self._begin(
                uow,
                scope,
                principal,
                request_id,
                kind + "." + operation,
                [str(entity_id), name, definition, expected_revision],
            )
            if prior:
                return ConfigurationDraft.model_validate(prior["draft_result"])
            await uow.evaluation_configuration.update(
                scope, kind, entity_id, expected_revision, name, definition, deleted=delete
            )
            result = ConfigurationDraft(
                id=entity_id,
                kind=kind,
                name=name,
                definition=definition,
                revision=expected_revision + 1,
            )
            return await self._finish(
                uow, scope, principal, request_id, fp, kind, operation, result
            )

    async def delete(self, scope, principal, *, kind, entity_id, expected_revision, request_id):
        async with self.uow_factory(self.auth(scope, principal, request_id)) as uow:
            fp, prior = await self._begin(
                uow,
                scope,
                principal,
                request_id,
                kind + ".delete",
                [str(entity_id), expected_revision],
            )
            if prior:
                return ConfigurationDraft.model_validate(prior["draft_result"])
            row = await uow.evaluation_configuration.draft(scope, kind, entity_id, lock=True)
            await uow.evaluation_configuration.update(
                scope,
                kind,
                entity_id,
                expected_revision,
                row["name"],
                row["definition"],
                deleted=True,
            )
            result = ConfigurationDraft(
                id=entity_id,
                kind=kind,
                name=row["name"],
                definition=row["definition"],
                revision=expected_revision + 1,
            )
            return await self._finish(uow, scope, principal, request_id, fp, kind, "delete", result)

    async def get_draft(self, scope, principal, kind, entity_id):
        async with self.uow_factory(self.auth(scope, principal)) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=False)
            return ConfigurationDraft.model_validate(
                await uow.evaluation_configuration.draft(scope, kind, entity_id)
            )

    async def list(
        self, scope, principal, kind, *, versions=False, cursor=None, limit=50, entity_id=None
    ):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_limit")
        context = [
            scope.model_dump(mode="json"),
            principal.user_id,
            kind,
            versions,
            str(entity_id) if entity_id else None,
        ]
        after = None
        if cursor:
            try:
                if len(cursor) > 4096:
                    raise ValueError()
                raw = base64.b64decode(
                    cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
                )
                payload, signature = raw[:-32], raw[-32:]
                if not hmac.compare_digest(
                    signature, hmac.new(self.cursor_secret, payload, hashlib.sha256).digest()
                ):
                    raise ValueError()
                decoded = json.loads(payload)
                if decoded["context"] != context:
                    raise ValueError()
                after = UUID(decoded["after"])
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError("invalid_cursor") from error
        async with self.uow_factory(self.auth(scope, principal)) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=False)
            rows = await uow.evaluation_configuration.list(
                scope, kind, versions=versions, after=after, limit=limit + 1, entity_id=entity_id
            )
            next_cursor = None
            if len(rows) > limit:
                payload = json.dumps(
                    {"context": context, "after": str(rows[limit - 1]["id"])},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                next_cursor = (
                    base64.urlsafe_b64encode(
                        payload + hmac.new(self.cursor_secret, payload, hashlib.sha256).digest()
                    )
                    .decode()
                    .rstrip("=")
                )
            return ConfigurationPage(
                items=tuple(ConfigurationSummary.model_validate(row) for row in rows[:limit]),
                next_cursor=next_cursor,
            )

    async def get_version(self, scope, principal, kind, version_id):
        async with self.uow_factory(self.auth(scope, principal)) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=False)
            return VERSIONS[kind].model_validate(
                await uow.evaluation_configuration.get_version(scope, kind, version_id)
            )

    async def publish(self, scope, principal, *, kind, entity_id, expected_revision, request_id):
        # The authority reader owns a separate connection; read before the business UoW.
        policy_pair = await self.policies.load_active_pair() if kind == "config" else None
        async with self.uow_factory(self.auth(scope, principal, request_id)) as uow:
            fp, prior = await self._begin(
                uow,
                scope,
                principal,
                request_id,
                kind + ".publish",
                [str(entity_id), expected_revision],
            )
            repo = uow.evaluation_configuration
            if prior:
                return VERSIONS[kind].model_validate(
                    await repo.get_version(scope, kind, UUID(prior["id"]))
                )
            row = await repo.draft(scope, kind, entity_id, lock=True)
            if row["revision"] != expected_revision:
                raise DatasetConflict("revision_conflict")
            definition = self.definition(kind, row["definition"])
            common = {
                "id": uuid4(),
                "entity_id": entity_id,
                "revision": row["revision"],
                "name": row["name"],
            }
            if kind == "config":
                snapshot = await self.resolved(
                    uow, scope, definition, principal, policy_pair=policy_pair
                )
                result = ConfigVersion(
                    **common, selection=definition, snapshot=snapshot, fingerprint=digest(snapshot)
                )
            elif kind == "rubric":
                judge = ConfigVersion.model_validate(
                    await repo.get_version(scope, "config", definition.judge_config_version)
                )
                if judge.selection.purpose != "evaluation_judge" or judge.selection.tool_names:
                    raise ValueError("independent_judge_required")
                result = RubricVersion(
                    **common,
                    **definition.model_dump(),
                    fingerprint=digest(definition.model_dump(mode="json")),
                )
            else:
                definition.settings.validate_limits(self.limits())
                dataset = await self.datasets.get_version_in_uow(
                    uow, scope, principal, definition.dataset_version
                )
                rubric = RubricVersion.model_validate(
                    await repo.get_version(scope, "rubric", definition.rubric_version)
                )
                validate_references(rubric, dataset.cases)
                dataset_metadata = await uow.evaluation_dataset.get_version(
                    scope, definition.dataset_version
                )
                proof = DatasetProof(
                    revision=dataset.revision,
                    membership_digest=dataset_membership_digest(dataset_metadata),
                    resources=dataset.pins,
                    reference_evidence=tuple(
                        (
                            str(case.id),
                            bool(case.reference_answer and case.reference_confirmed),
                            case.applicable_dimensions,
                        )
                        for case in dataset.cases
                    ),
                )
                for config_id in definition.config_versions:
                    config = ConfigVersion.model_validate(
                        await repo.get_version(scope, "config", config_id)
                    )
                    if (
                        config.selection.purpose != "evaluation_subject"
                        or config_id == rubric.judge_config_version
                    ):
                        raise ValueError("independent_judge_required")
                    reference = config.selection.external_contract_ref
                    if reference and not (
                        (
                            reference.kind == "recording"
                            and definition.mode == "recorded"
                            and reference.version_id in definition.recording_versions
                        )
                        or (
                            reference.kind == "environment"
                            and definition.mode == "isolated"
                            and reference.version_id == definition.environment_version
                        )
                    ):
                        raise ValueError("contract_source_mismatch")
                quantity = validate_matrix(
                    len(dataset.cases), len(definition.config_versions), definition.settings.repeat
                )
                if quantity > definition.settings.max_results:
                    raise ValueError("max_results_exceeded")
                result = SuiteVersion(
                    **common,
                    **definition.model_dump(),
                    quantity=quantity,
                    dataset_proof=proof,
                    fingerprint=digest(definition.model_dump(mode="json")),
                )
            await repo.publish(scope, kind, result)
            if kind == "config":
                await uow.resource_pins.acquire(
                    scope, "config_version", str(result.id), definition.resources
                )
            return await self._finish(
                uow, scope, principal, request_id, fp, kind, "publish", result
            )
