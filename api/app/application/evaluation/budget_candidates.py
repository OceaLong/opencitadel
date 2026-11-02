"""Private fixed candidate boundary; E06 binds this to durable admission in Pass C/D."""

from app.application.evaluation.budget_service import BudgetAuthority
from app.domain.evaluation.configuration import digest
from app.domain.json_values import deep_freeze_json
from app.domain.models.inference import ChatModelSettings


class FrozenBudgetCandidates:
    def __init__(self, inventory, proof):
        self.inventory = inventory
        if proof.get("inventory") != inventory.fingerprint or not proof.get("candidates"):
            raise ValueError("budget_configuration_proof_unavailable")
        authority = BudgetAuthority(inventory)
        for candidate in proof["candidates"]:
            expected = authority.configuration(candidate)
            if any(candidate.get(key) != value for key, value in expected.items()):
                raise ValueError("budget_configuration_proof_unavailable")
        self.proof = deep_freeze_json(proof)

    def for_context_summary(self, *, output_tokens):
        """Derive a bounded output setting; retain every fixed fallback candidate.

        This does not reserve capacity or authorize a send. The context Activity
        must bind the derived proof to its independent dispatch and namespace.
        """
        return self._for_context(output_tokens=output_tokens, purpose="summary", maximum=4096)

    def for_context_model(self, *, output_tokens):
        """Bind the definition's explicit output setting to the original candidate proof.

        This is the same policy override used by ordinary context admission.
        Evaluation keeps its original configuration evidence and records this
        derived physical setting, without changing model/fallback identities.
        """
        if type(output_tokens) is not int or not 256 <= output_tokens <= 32768:
            raise ValueError("CONTEXT_MODEL_OUTPUT_INVALID")
        if all(item["output"] == output_tokens for item in self.proof["candidates"]):
            return self
        return self._for_context(output_tokens=output_tokens, purpose="model", maximum=32768)

    def _for_context(self, *, output_tokens, purpose, maximum):
        from copy import deepcopy

        from app.domain.evaluation.budget_capabilities import PROFILES, BudgetProfile

        if type(output_tokens) is not int or not 256 <= output_tokens <= maximum:
            raise ValueError(f"CONTEXT_{purpose.upper()}_OUTPUT_INVALID")
        proof = deepcopy(self.proof)
        parent = digest(self.proof)
        authority = BudgetAuthority(self.inventory)
        for candidate in proof["candidates"]:
            profile = BudgetProfile.model_validate(candidate["profile"])
            if output_tokens > PROFILES[profile.profile][4]:
                raise ValueError("CONTEXT_OUTPUT_PROFILE_UNSUPPORTED")
            candidate["settings"]["max_output_tokens"] = output_tokens
            candidate.update(authority.configuration(candidate))
        proof["context_subpurpose"] = purpose
        proof["parent_candidate_proof_digest"] = parent
        return FrozenBudgetCandidates(self.inventory, proof)

    def validate(self, model, candidate, *, effective=False):
        identity = {
            "model_id": model.id,
            "endpoint_id": model.endpoint.id,
            "configured_model": model.model_name,
            "provider": model.provider.value,
            "endpoint_digest": digest(model.base_url),
        }
        if (
            identity != candidate["identity"]
            or model.extra_params
            or model.capabilities.model_dump(mode="json") != candidate["capabilities"]
            or model.model.settings.model_dump(mode="json")
            != candidate["settings" if effective else "base_settings"]
            or (model.provider.value != "ollama" and not model.credential.strip())
            or (
                "price" in candidate
                and candidate["price"]
                != {
                    "input": model.model.input_price_per_million or None,
                    "output": model.model.output_price_per_million or None,
                }
            )
        ):
            raise ValueError("budget_candidate_changed")

    def primary(self, model):
        candidate = self.proof["candidates"][0]
        self.validate(model, candidate)
        resolved = model.model_copy(deep=True)
        resolved.model.settings = ChatModelSettings.model_validate(candidate["settings"])
        return resolved

    async def resolve(self, service, scope, primary, *, require_vision, thinking_enabled):
        if require_vision or thinking_enabled or service is None:
            raise ValueError("budget_input_mode_unsupported")
        candidates = self.proof["candidates"]
        self.validate(primary, candidates[0], effective=True)
        chain = []
        for candidate in candidates:
            model = await service.resolve_chat(candidate["identity"]["model_id"], scope=scope)
            self.validate(model, candidate)
            model = model.model_copy(deep=True)
            model.model.settings = ChatModelSettings.model_validate(candidate["settings"])
            chain.append(model)
        return chain


class BudgetPayloadGuard:
    """Validate final transformed payload before the downstream durable guard."""

    def __init__(self, candidates, downstream):
        self.candidates, self.downstream = candidates, downstream

    async def before_send(self, model, payload):
        from app.domain.evaluation.budget_capabilities import BudgetProfile

        candidate = next(
            (
                item
                for item in self.candidates.proof["candidates"]
                if item["identity"]["model_id"] == model.id
            ),
            None,
        )
        if candidate is None:
            raise ValueError("budget_candidate_changed")
        self.candidates.validate(model, candidate, effective=True)
        BudgetProfile.model_validate(candidate["profile"]).bound(
            payload, output=candidate["output"]
        )
        return await self.downstream.before_send(model, payload)

    async def after_send(self, identity, usage, revision):
        await self.downstream.after_send(identity, usage, revision)

    async def after_completion(self, identity, revision):
        if hasattr(self.downstream, "after_completion"):
            await self.downstream.after_completion(identity, revision)
