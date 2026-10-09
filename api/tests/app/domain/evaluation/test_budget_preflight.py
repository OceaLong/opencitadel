from types import SimpleNamespace

import pytest

from tests.app.domain.evaluation.test_budget_capabilities import registry


def metadata():
    from app.domain.evaluation.configuration import digest

    return {
        "identity": {
            "endpoint_id": "endpoint-1",
            "provider": "anthropic",
            "configured_model": "claude-haiku-4-5-20251001",
            "endpoint_digest": digest("https://api.anthropic.com"),
        },
        "settings": {"max_output_tokens": 100},
    }


def test_publication_captures_fixed_bound_profile_and_money_coverage():
    from app.application.evaluation.budget_service import BudgetAuthority

    authority = BudgetAuthority(registry())
    proof = authority.configuration(metadata())
    assert proof["inventory"] == registry().fingerprint
    assert proof["tokens"] == 200100
    assert proof["money"] == "0.4005"


@pytest.mark.asyncio
async def test_preflight_fails_when_single_call_cannot_enter_or_profile_changed():
    from app.application.evaluation.budget_service import BudgetAuthority

    authority = BudgetAuthority(registry())
    config = SimpleNamespace(snapshot={**metadata(), "budget": authority.configuration(metadata())})
    suite = SimpleNamespace(settings=SimpleNamespace(token_budget=100, money_budget=None))
    result = await authority.check(None, suite, (config,), for_start=True)
    assert not result.ready
    assert "budget_single_call_exceeds_limit" in result.errors
    suite.settings.token_budget = 300000
    result = await authority.check(None, suite, (config,), for_start=True)
    assert result.ready
    config.snapshot["budget"]["inventory"] = "stale"
    assert not (await authority.check(None, suite, (config,), for_start=True)).ready


@pytest.mark.asyncio
async def test_legacy_configuration_has_no_hard_budget_authority():
    from app.application.evaluation.budget_service import BudgetAuthority

    authority = BudgetAuthority(registry())
    result = await authority.check(
        None,
        SimpleNamespace(settings=SimpleNamespace(token_budget=300000, money_budget=None)),
        (SimpleNamespace(snapshot=metadata()),),
        for_start=False,
    )
    assert not result.ready
