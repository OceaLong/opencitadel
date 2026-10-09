"""Trusted configuration proof and metadata-only budget preflight.

Reservation transport is infrastructure-owned; this module never accepts public
callers' arbitrary bounds as authority. E06 admission must reuse the checked proof.
"""

from decimal import Decimal

from app.application.evaluation.preflight import DependencyEvidence
from app.domain.evaluation.budget_capabilities import PROFILES, BudgetInventory
from app.domain.evaluation.configuration import digest


class BudgetAuthority:
    def __init__(self, inventory: BudgetInventory):
        self.inventory = inventory

    def configuration(self, metadata):
        identity = metadata["identity"]
        endpoint = self.inventory.endpoint_for(identity)
        if endpoint is None or digest(endpoint.origin) != identity["endpoint_digest"]:
            raise ValueError("budget_endpoint_identity_mismatch")
        profile = self.inventory.resolve(
            identity["endpoint_id"],
            identity["provider"],
            identity["configured_model"],
            endpoint.origin,
            configured_price=metadata.get("price"),
        )
        output = metadata["settings"].get("max_output_tokens")
        provider = PROFILES[profile.profile][0]
        payload = (
            {"model": profile.model, "max_tokens": output, "messages": []}
            if provider == "anthropic"
            else {"generationConfig": {"maxOutputTokens": output}, "contents": []}
        )
        if provider == "openai":
            payload = {"model": profile.model, "messages": [], "max_completion_tokens": output}
        bound = profile.bound(payload, output=output)
        return {
            "inventory": self.inventory.fingerprint,
            "profile": profile.model_dump(mode="json"),
            "tokens": bound.tokens,
            "money": str(bound.money) if bound.money is not None else None,
            "output": output,
            "source": bound.source,
        }

    async def candidates_in_uow(self, uow, scope, selection, primary, *, policy):
        from dataclasses import dataclass

        from app.domain.models.inference_candidates import ordered_candidates

        @dataclass(frozen=True)
        class Candidate:
            body: dict

            @property
            def id(self):
                return self.body["identity"]["model_id"]

            @property
            def provider(self):
                return self.body["identity"]["provider"]

        metadata = (
            await uow.evaluation_configuration.candidate_metadata(scope)
            if policy.fallback_enabled or policy.fallback_on_quota_exceeded
            else ()
        )
        chain = ordered_candidates(
            Candidate(primary),
            [Candidate(item) for item in metadata],
            policy,
            eligible=lambda item: item.provider == "ollama" or item.body["credential_configured"],
            thinking=lambda item: False,
            thinking_enabled=False,
        )
        proofs = []
        for candidate in chain:
            body = candidate.body
            if body["extra_params_present"]:
                raise ValueError("budget_candidate_parameters_unsupported")
            settings = dict(body["settings"])
            if selection.temperature is not None:
                settings["temperature"] = selection.temperature
            if selection.max_output_tokens is not None:
                settings["max_output_tokens"] = selection.max_output_tokens
            proof = self.configuration({**body, "settings": settings})
            proofs.append(
                {
                    **proof,
                    "identity": body["identity"],
                    "base_settings": body["settings"],
                    "settings": settings,
                    "capabilities": body["capabilities"],
                    "credential_configured": body["credential_configured"],
                    **(
                        {
                            "price": {
                                key: body.get("price", {}).get(key) for key in ("input", "output")
                            }
                        }
                        if proof["profile"]["profile"] == "acceptance-chat-v1"
                        else {}
                    ),
                }
            )
        return {"inventory": self.inventory.fingerprint, "candidates": proofs}

    async def check(self, scope, suite, configs, *, for_start):
        errors, prices = [], []
        for config in configs:
            try:
                saved = config.snapshot.get("budget")
                if saved is None or saved.get("inventory") != self.inventory.fingerprint:
                    raise ValueError("budget_configuration_proof_unavailable")
                candidates = saved.get("candidates")
                if candidates is None:
                    expected = self.configuration(config.snapshot)
                    if saved != expected:
                        raise ValueError("budget_configuration_proof_unavailable")
                    candidates = [expected]
                if not candidates:
                    raise ValueError("budget_configuration_proof_unavailable")
                for expected in candidates:
                    if "identity" in expected:
                        actual = self.configuration(expected)
                        if any(expected.get(key) != value for key, value in actual.items()):
                            raise ValueError("budget_configuration_proof_unavailable")
                    if expected["tokens"] > suite.settings.token_budget:
                        errors.append("budget_single_call_exceeds_limit")
                    money = expected["money"]
                    prices.append(money is not None)
                    if suite.settings.money_budget is not None:
                        if money is None:
                            errors.append("budget_price_coverage_incomplete")
                        elif Decimal(money) > Decimal(str(suite.settings.money_budget)):
                            errors.append("budget_single_call_exceeds_money")
            except (KeyError, ValueError):
                errors.append("budget_configuration_proof_unavailable")
        if not configs:
            errors.append("budget_configuration_unavailable")
        return DependencyEvidence(
            ready=not errors,
            revision=self.inventory.fingerprint,
            errors=tuple(sorted(set(errors))),
            price_coverage="complete"
            if prices and all(prices)
            else "partial"
            if any(prices)
            else "unknown",
        )

    async def check_in_uow(self, scope, suite, configs, *, for_start, uow, principal):
        # All input configuration metadata is already loaded in the caller UoW;
        # never perform a second same-pool checkout or a provider count request.
        await uow.evaluation_dataset.authorize(scope, principal, write=False)
        checked = await self.check(scope, suite, configs, for_start=for_start)
        policy = await uow.evaluation_physical_policy.active()
        errors = list(checked.errors)
        if policy is None or not policy.hard_evaluation_available:
            errors.append("budget_physical_policy_unavailable")
        return checked.model_copy(
            update={
                "ready": not errors,
                "errors": tuple(sorted(set(errors))),
                "revision": digest(
                    {
                        "inventory": self.inventory.fingerprint,
                        "physical_policy": policy.model_dump(mode="json") if policy else None,
                    }
                ),
            }
        )


class _SendPermit:
    """One local physical send; create only from a newly committed reservation.

    Cross-worker uniqueness is the repository's durable fresh=False result. This
    object adds same-worker reuse protection; it does not commit a caller UoW.
    """

    def __init__(self):
        from threading import Lock

        self._lock = Lock()
        self._consumed = False

    def consume(self):
        with self._lock:
            if self._consumed:
                raise ValueError("budget_already_dispatched")
            self._consumed = True


def require_fresh_permit(result):
    """Idempotent reserve redelivery is a receipt, never another send permit."""
    if result.get("fresh") is not True:
        raise ValueError("budget_already_dispatched")
    return _SendPermit()


class BudgetDemandFactory:
    """Server-owned policy and endpoint pool map; callers cannot supply limits.

    Bound amounts originate from the verified candidate profile. Future durable
    batch bindings add their fixed budgets; public HTTP models never call this API.
    Unknown endpoints collide in a shared conservative pool, not per-alias pools.
    """

    def __init__(self, policy, *, provider_pools=None):
        from types import MappingProxyType

        self.policy = policy
        self.provider_pools = MappingProxyType(dict(provider_pools or {}))

    def provider_buckets(self, *, endpoint_id, provider_kind=None):
        import hashlib

        from app.domain.evaluation.budget import BudgetBucket
        from app.domain.models.inference import InferenceProvider

        if provider_kind is not None and not isinstance(provider_kind, InferenceProvider):
            raise ValueError("budget_provider_authority_required")
        account = self.provider_pools.get(endpoint_id)
        account_key = (
            "unknown"
            if account is None
            else "sha256:" + hashlib.sha256(account.encode()).hexdigest()
        )
        kind = provider_kind.value if provider_kind is not None else "unknown"
        return (
            BudgetBucket(key="1:provider:kind:" + kind, slots=self.policy.provider_concurrency),
            BudgetBucket(
                key="1:provider:account:" + account_key, slots=self.policy.provider_concurrency
            ),
        )

    def physical(self, authorization, *, endpoint_id, tokens, money, provider_kind=None):
        from app.domain.evaluation.budget import BudgetBucket, BudgetDemand, BudgetPrincipal
        from app.domain.models.authorization import AuthorizationMode

        if authorization.mode != AuthorizationMode.USER or authorization.principal is None:
            raise ValueError("budget_requester_authority_required")
        principal, scope = authorization.principal, authorization.scope
        if scope is None:
            raise ValueError("budget_scope_authority_required")
        return BudgetDemand(
            scope="team:" + scope.team_id if scope.team_id else "user:" + principal.user_id,
            requester=principal.user_id,
            principal=BudgetPrincipal(
                user_id=principal.user_id,
                token_version=principal.token_version,
                global_role=principal.global_role,
                team_role=principal.team_roles.get(scope.team_id),
            ),
            purpose="production",
            policy_revision=self.policy.revision,
            tokens=tokens,
            money=money,
            buckets=(
                BudgetBucket(key="0:global", slots=self.policy.global_concurrency),
                *self.provider_buckets(endpoint_id=endpoint_id, provider_kind=provider_kind),
                BudgetBucket(key="2:user:" + principal.user_id, slots=self.policy.user_concurrency),
            ),
        )
