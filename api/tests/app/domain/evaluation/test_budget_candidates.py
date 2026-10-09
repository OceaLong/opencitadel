from dataclasses import dataclass

from app.domain.runtime_policy.execution import ModelResiliencePolicy


@dataclass
class Candidate:
    id: str
    provider: str
    eligible: bool = True
    thinking: bool = False


def test_shared_candidate_order_preserves_quota_defaults_and_thinking():
    from app.domain.models.inference_candidates import ordered_candidates

    primary = Candidate("primary", "a")
    items = [
        Candidate("cross", "b"),
        Candidate("plain", "a"),
        Candidate("think", "a", thinking=True),
        Candidate("missing", "a", eligible=False),
        Candidate("primary", "a"),
        Candidate("cross", "b"),
    ]
    assert [
        x.id
        for x in ordered_candidates(
            primary,
            items,
            ModelResiliencePolicy(),
            eligible=lambda x: x.eligible,
            thinking=lambda x: x.thinking,
            thinking_enabled=True,
        )
    ] == ["primary", "think", "plain", "cross"]
    assert ordered_candidates(
        primary,
        items,
        ModelResiliencePolicy(fallback_on_quota_exceeded=False),
        eligible=lambda x: True,
        thinking=lambda x: False,
        thinking_enabled=False,
    ) == [primary]


def test_frozen_runtime_candidates_resolve_only_published_ids_and_reject_current_drift():
    import asyncio

    from app.application.evaluation.budget_candidates import FrozenBudgetCandidates
    from app.domain.evaluation.budget_capabilities import BudgetInventory
    from app.domain.evaluation.configuration import digest
    from tests.app.infrastructure.external.llm.inference_model_factory import resolved_chat_model

    first = resolved_chat_model(
        model_name="gpt-4.1-2025-04-14", base_url="https://api.openai.com/v1"
    )
    first.model.id = "fixed"
    inventory = BudgetInventory.model_validate(
        {
            "revision": "test",
            "endpoints": {
                first.endpoint.id: {"provider": "openai", "origin": first.base_url, "pool": "one"}
            },
            "profiles": [
                {
                    "endpoint_id": first.endpoint.id,
                    "model": first.model_name,
                    "profile": "openai-gpt41-chat-v1",
                }
            ],
        }
    )
    metadata = {
        "identity": {
            "model_id": first.id,
            "endpoint_id": first.endpoint.id,
            "configured_model": first.model_name,
            "provider": "openai",
            "endpoint_digest": digest(first.base_url),
        },
        "settings": first.model.settings.model_dump(mode="json"),
        "base_settings": first.model.settings.model_dump(mode="json"),
        "capabilities": first.capabilities.model_dump(mode="json"),
        "credential_configured": True,
    }
    from app.application.evaluation.budget_service import BudgetAuthority

    metadata.update(BudgetAuthority(inventory).configuration(metadata))
    authority = FrozenBudgetCandidates(
        inventory, {"inventory": inventory.fingerprint, "candidates": [metadata]}
    )

    class Resolver:
        async def resolve_chat(self, model_id, *, scope):
            assert model_id == "fixed"
            return first

        async def list_resolved_chat_models(self, **kwargs):
            raise AssertionError("frozen authority must never discover new fallbacks")

    assert (
        asyncio.run(
            authority.resolve(Resolver(), None, first, require_vision=False, thinking_enabled=False)
        )[0].id
        == "fixed"
    )
    first.model.model_name = "changed"
    import pytest

    with pytest.raises(ValueError, match="budget_candidate_changed"):
        asyncio.run(
            authority.resolve(Resolver(), None, first, require_vision=False, thinking_enabled=False)
        )


def test_configured_fixture_prices_survive_candidate_freezing_and_reject_drift():
    import asyncio
    from copy import deepcopy

    import pytest

    from app.application.evaluation.budget_candidates import FrozenBudgetCandidates
    from app.application.evaluation.budget_service import BudgetAuthority
    from app.domain.evaluation.budget_capabilities import BudgetInventory
    from app.domain.evaluation.configuration import ConfigSelection, digest
    from tests.app.infrastructure.external.llm.inference_model_factory import resolved_chat_model

    model = resolved_chat_model(
        model_name="acceptance-chat",
        base_url="http://acceptance-inference:8080/v1",
        max_output_tokens=4096,
    )
    model.model.input_price_per_million = 1
    model.model.output_price_per_million = 2
    metadata = {
        "identity": {
            "model_id": model.id,
            "endpoint_id": model.endpoint.id,
            "configured_model": model.model_name,
            "provider": "openai",
            "endpoint_digest": digest(model.base_url),
        },
        "settings": model.model.settings.model_dump(mode="json"),
        "capabilities": model.capabilities.model_dump(mode="json"),
        "credential_configured": True,
        "extra_params_present": False,
        "price": {"input": 1, "output": 2},
    }
    inventory = BudgetInventory(revision="fixture", acceptance_fixture=True)
    proof = asyncio.run(
        BudgetAuthority(inventory).candidates_in_uow(
            None,
            None,
            ConfigSelection(model_id=model.id),
            metadata,
            policy=ModelResiliencePolicy(fallback_enabled=False, fallback_on_quota_exceeded=False),
        )
    )
    candidate = proof["candidates"][0]
    assert candidate["price"] == {"input": 1, "output": 2}
    assert candidate["money"] == "0.270336"
    frozen = FrozenBudgetCandidates(inventory, proof)
    assert frozen.primary(model).id == model.id
    tampered = deepcopy(proof)
    tampered["candidates"][0]["price"]["input"] = 0
    with pytest.raises(ValueError, match="budget_configuration_proof_unavailable"):
        FrozenBudgetCandidates(inventory, tampered)
    model.model.output_price_per_million = 3
    with pytest.raises(ValueError, match="budget_candidate_changed"):
        frozen.primary(model)
