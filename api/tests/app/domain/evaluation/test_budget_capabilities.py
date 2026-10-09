from decimal import Decimal

import pytest


def registry():
    from app.domain.evaluation.budget_capabilities import BudgetInventory

    return BudgetInventory.model_validate(
        {
            "revision": "inventory-1",
            "endpoints": {
                "endpoint-1": {
                    "provider": "anthropic",
                    "origin": "https://api.anthropic.com",
                    "pool": "account-1",
                }
            },
            "profiles": [
                {
                    "endpoint_id": "endpoint-1",
                    "model": "claude-haiku-4-5-20251001",
                    "profile": "anthropic-haiku45-messages-v1",
                    "price": {
                        "input_per_million": "1",
                        "output_per_million": "5",
                        "cache_read_per_million": ".1",
                        "cache_write_per_million": "2",
                        "reasoning_uses_output_rate": True,
                    },
                }
            ],
        }
    )


def test_verified_provider_ceiling_includes_full_input_and_enforced_output():
    profile = registry().resolve(
        "endpoint-1", "anthropic", "claude-haiku-4-5-20251001", "https://api.anthropic.com"
    )
    bound = profile.bound({"model": profile.model, "max_tokens": 4096, "messages": []}, output=4096)
    assert bound.tokens == 204096
    assert bound.money == Decimal(".42048")
    assert bound.source.startswith("https://platform.claude.com/")


@pytest.mark.parametrize(
    "change",
    [
        {"max_tokens": 8192},
        {"n": 2},
        {"model": "untrusted-alias"},
        {"tools": [{"type": "web_search_20250305", "name": "search"}]},
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {"type": "url", "url": "https://example.com"},
                        }
                    ],
                }
            ]
        },
    ],
)
def test_transformed_payload_cannot_escape_profile(change):
    profile = registry().profiles[0]
    with pytest.raises(ValueError, match="budget_"):
        profile.bound(
            {"model": profile.model, "max_tokens": 4096, "messages": [], **change}, output=4096
        )


def test_unknown_rates_do_not_become_free_money():
    profile = registry().profiles[0].model_copy(update={"price": None})
    assert (
        profile.bound({"model": profile.model, "max_tokens": 10, "messages": []}, output=10).money
        is None
    )


def test_caller_model_hint_cannot_shrink_capability_ceiling():
    from app.domain.evaluation.budget_capabilities import BudgetProfile

    with pytest.raises(ValueError, match="Extra inputs"):
        BudgetProfile.model_validate(
            {
                "endpoint_id": "endpoint-1",
                "model": "claude-haiku-4-5-20251001",
                "profile": "anthropic-haiku45-messages-v1",
                "input_tokens": 100,
            }
        )


def test_aliases_share_deployment_account_pool_and_origin_drift_denies():
    inventory = registry()
    assert inventory.endpoints["endpoint-1"].pool == "account-1"
    with pytest.raises(ValueError, match="identity"):
        inventory.resolve(
            "endpoint-1", "anthropic", "claude-haiku-4-5-20251001", "https://proxy.invalid"
        )


def test_native_openai_completion_bound_and_unsupported_modes():
    from app.domain.evaluation.budget_capabilities import BudgetProfile

    profile = BudgetProfile.model_validate(
        {
            "endpoint_id": "native-openai",
            "model": "gpt-4.1-2025-04-14",
            "profile": "openai-gpt41-chat-v1",
        }
    )
    payload = {
        "model": profile.model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_completion_tokens": 100,
    }
    assert profile.bound(payload, output=100).tokens == 1047676
    for change in (
        {"n": 2},
        {"max_tokens": 100},
        {"prediction": {}},
        {"audio": {}},
        {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]},
    ):
        with pytest.raises(ValueError, match="budget_"):
            profile.bound({**payload, **change}, output=100)


def test_deployment_loader_reads_validated_inventory_and_rejects_drift(tmp_path):
    from app.infrastructure.evaluation.budget_inventory import load_budget_inventory

    path = tmp_path / "inventory.json"
    path.write_text(registry().model_dump_json())
    assert load_budget_inventory(str(path)).fingerprint == registry().fingerprint
    path.write_text('{"revision":"x","unknown":true}')
    with pytest.raises(ValueError, match="Extra inputs"):
        load_budget_inventory(str(path))
    assert load_budget_inventory("") is None


def test_checked_in_inventory_sample_is_executable_deployment_configuration():
    from pathlib import Path

    from app.composition.evaluation import build_budget_authority
    from core.config import DeploymentSettings

    path = Path(__file__).resolve().parents[5] / "deploy/evaluation/budget-inventory.example.json"
    settings = DeploymentSettings(evaluation_budget_inventory_path=str(path))
    authority = build_budget_authority(settings)
    assert {p.profile for p in authority.inventory.profiles} == {
        "openai-gpt41-chat-v1",
        "anthropic-haiku45-messages-v1",
        "gemini25-flash-generate-content-v1",
    }
    assert all(p.price is None for p in authority.inventory.profiles)


def test_acceptance_profile_requires_deployment_enable_and_exact_current_origin(tmp_path):
    from app.application.evaluation.budget_service import BudgetAuthority
    from app.domain.evaluation.configuration import digest
    from app.infrastructure.evaluation.budget_inventory import load_budget_inventory

    path = tmp_path / "inventory.json"
    path.write_text('{"revision":"e12-fixture-v1","acceptance_fixture":true}')
    with pytest.raises(ValueError, match="acceptance"):
        load_budget_inventory(str(path))
    inventory = load_budget_inventory(str(path), allow_acceptance=True)
    authority = BudgetAuthority(inventory)
    identity = {
        "endpoint_id": "dynamic-owned-id",
        "provider": "openai",
        "configured_model": "acceptance-chat",
        "endpoint_digest": digest("http://acceptance-inference:8080/v1"),
    }
    proof = authority.configuration({"identity": identity, "settings": {"max_output_tokens": 4096}})
    assert proof["money"] == "0"
    assert proof["tokens"] == 262144 + 4096
    for change in (
        {"configured_model": "other"},
        {"endpoint_digest": digest("http://attacker.test/v1")},
        {"provider": "anthropic"},
    ):
        with pytest.raises(ValueError, match="budget_"):
            authority.configuration(
                {"identity": {**identity, **change}, "settings": {"max_output_tokens": 4096}}
            )
    profile = inventory.resolve(
        identity["endpoint_id"], "openai", "acceptance-chat", "http://acceptance-inference:8080/v1"
    )
    from app.infrastructure.external.llm.base_llm import normalize_usage

    assert profile.price is not None
    assert profile.price.provenance == "explicit"
    assert all(
        getattr(profile.price, name) == Decimal(0)
        for name in (
            "input_per_million",
            "output_per_million",
            "cache_read_per_million",
            "cache_write_per_million",
            "reasoning_per_million",
        )
    )
    complete = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 0},
    }
    assert profile.price.cost(normalize_usage(complete)) == Decimal(0)
    assert profile.price.cost(normalize_usage(None)) is None
    assert (
        profile.price.cost(
            normalize_usage({"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})
        )
        is None
    )
    assert profile.price.cost(normalize_usage({**complete, "total_tokens": 119})) is None
    with pytest.raises(ValueError, match="budget_"):
        profile.bound(
            {
                "model": "acceptance-chat",
                "messages": [{"role": "user", "content": "x" * 1048577}],
                "max_tokens": 4096,
            },
            output=4096,
        )


def test_native_inventory_fingerprint_preserves_existing_proofs():
    from app.domain.evaluation.configuration import digest

    inventory = registry()
    assert inventory.fingerprint == digest(
        inventory.model_dump(mode="json", exclude={"acceptance_fixture"})
    )


@pytest.mark.parametrize(
    ("configured", "bound_money"),
    [
        ({"input": 1, "output": 2}, Decimal(".270336")),
        ({"input": None, "output": None}, Decimal(0)),
        ({"input": 1, "output": None}, None),
        ({"input": None, "output": 2}, None),
    ],
)
def test_acceptance_configured_price_drives_bound_and_usage_snapshot(configured, bound_money):
    from app.application.evaluation.budget_service import BudgetAuthority
    from app.application.services.execution_usage_service import configuration_snapshot
    from app.domain.evaluation.budget_capabilities import BudgetInventory
    from app.domain.evaluation.configuration import digest
    from app.domain.models.execution_usage import PriceSnapshot
    from app.infrastructure.external.llm.base_llm import normalize_usage
    from tests.app.infrastructure.external.llm.inference_model_factory import resolved_chat_model

    inventory = BudgetInventory(revision="fixture", acceptance_fixture=True)
    model = resolved_chat_model(
        model_name="acceptance-chat",
        base_url="http://acceptance-inference:8080/v1",
        max_output_tokens=4096,
    )
    proof = BudgetAuthority(inventory).configuration(
        {
            "identity": {
                "endpoint_id": model.endpoint.id,
                "provider": "openai",
                "configured_model": model.model_name,
                "endpoint_digest": digest(model.base_url),
            },
            "settings": model.model.settings.model_dump(mode="json"),
            "price": configured,
        }
    )
    assert proof["money"] == (str(bound_money) if bound_money is not None else None)
    price = PriceSnapshot.model_validate(proof["profile"]["price"])
    snapshot = configuration_snapshot(
        model, policy_revision="fixture-test", tool_fingerprint=None, price_snapshot=price
    )
    assert snapshot["price"] == price.model_dump(mode="json")
    usage = normalize_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
    )
    cost = price.cost(usage)
    assert cost == (Decimal(".00014") if configured == {"input": 1, "output": 2} else bound_money)
    assert price.cost(normalize_usage(None)) is None


def test_native_profile_does_not_accept_configured_fixture_price_override():
    inventory = registry()
    profile = inventory.resolve(
        "endpoint-1",
        "anthropic",
        "claude-haiku-4-5-20251001",
        "https://api.anthropic.com",
        configured_price={"input": 0, "output": 0},
    )
    assert profile == inventory.profiles[0]
