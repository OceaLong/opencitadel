from decimal import Decimal

import pytest


def test_inflight_reservations_count_against_budget():
    from app.domain.evaluation.budget import reservation_fits

    assert not reservation_fits(100, 40, 50, 20)
    assert reservation_fits(100, 40, 50, 10)


@pytest.mark.parametrize("values", [(-1, 0, 0, 0), (1, -1, 0, 0), (1, 0, -1, 0), (1, 0, 0, -1)])
def test_negative_budget_values_are_rejected(values):
    from app.domain.evaluation.budget import reservation_fits

    with pytest.raises(ValueError, match="negative"):
        reservation_fits(*values)


def test_decimal_budget_has_no_float_rounding():
    from app.domain.evaluation.budget import reservation_fits

    assert reservation_fits(Decimal(".3"), Decimal(".1"), Decimal(".1"), Decimal(".1"))
    assert not reservation_fits(Decimal(".3"), Decimal(".1"), Decimal(".1"), Decimal(".100001"))


def test_typed_demand_is_strict_canonical_and_rejects_duplicate_buckets():
    from app.domain.evaluation.budget import BudgetDemand

    raw = {
        "scope": "user:u",
        "requester": "u",
        "purpose": "production",
        "tokens": 10,
        "money": None,
        "buckets": [{"key": "3:workspace:user:u", "slots": 2}, {"key": "0:global", "slots": 4}],
    }
    value = BudgetDemand.model_validate(raw)
    assert value.buckets[0].key == "0:global"
    with pytest.raises(ValueError, match="duplicate"):
        BudgetDemand.model_validate({**raw, "buckets": [raw["buckets"][1], raw["buckets"][1]]})
    for tokens in (True, 1.5, "10", -1):
        with pytest.raises(ValueError, match="tokens"):
            BudgetDemand.model_validate({**raw, "tokens": tokens})
    with pytest.raises(ValueError, match="extra"):
        BudgetDemand.model_validate({**raw, "allow_unbounded": True})
    with pytest.raises(ValueError, match="frozen"):
        value.buckets[0].slots = 999


def test_reservation_service_derives_scope_and_refuses_second_send_permit():
    from app.application.evaluation.budget_service import BudgetDemandFactory, require_fresh_permit
    from app.domain.evaluation.budget import BudgetPolicy
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope, Principal

    auth = AuthorizationContext.for_principal(
        Principal(user_id="u"), scope=OwnerScope.personal("u")
    )
    factory = BudgetDemandFactory(
        BudgetPolicy(global_concurrency=7, user_concurrency=3, provider_concurrency=2),
        provider_pools={"endpoint": "account"},
    )
    value = factory.physical(auth, endpoint_id="endpoint", tokens=None, money=None)
    assert value.scope == "user:u"
    assert value.requester == "u"
    assert [b.key for b in value.buckets] == [
        "0:global",
        "1:provider:account:sha256:" + __import__("hashlib").sha256(b"account").hexdigest(),
        "1:provider:kind:unknown",
        "2:user:u",
    ]
    assert require_fresh_permit({"fresh": True})
    with pytest.raises(ValueError, match="already_dispatched"):
        require_fresh_permit({"fresh": False})


def test_registry_endpoint_mapping_is_deeply_immutable():
    from tests.app.domain.evaluation.test_budget_capabilities import registry

    inventory = registry()
    with pytest.raises(TypeError, match="immutable"):
        inventory.endpoints["rogue"] = inventory.endpoints["endpoint-1"]
    with pytest.raises(ValueError, match="frozen"):
        inventory.endpoints["endpoint-1"].pool = "rogue"


def test_copying_inventory_keeps_nested_map_immutable():
    from tests.app.domain.evaluation.test_budget_capabilities import registry

    copied = registry().model_copy(deep=True)
    with pytest.raises(TypeError, match="immutable"):
        copied.endpoints.clear()


@pytest.mark.asyncio
async def test_signing_revalidates_typed_model_copy_before_database_access():
    from app.domain.evaluation.budget import BudgetDemand
    from app.infrastructure.repositories.db_evaluation_budget_repository import (
        DBEvaluationBudgetRepository,
    )

    class NoDatabase:
        async def scalar(self, *args, **kwargs):
            raise AssertionError("database touched before demand validation")

    value = BudgetDemand(
        scope="user:u",
        requester="u",
        purpose="production",
        tokens=1,
        buckets=({"key": "0:global", "slots": 1},),
    ).model_copy(update={"tokens": -1})
    repo = DBEvaluationBudgetRepository(NoDatabase(), signing_secret="unit-only")
    with pytest.raises(ValueError, match="tokens"):
        await repo.reserve("00000000-0000-0000-0000-000000000001", value)
    with pytest.raises(TypeError, match="trusted_budget_demand"):
        await repo.reserve("00000000-0000-0000-0000-000000000001", {})


def test_even_one_fresh_receipt_can_authorize_only_one_local_send():
    from app.application.evaluation.budget_service import require_fresh_permit

    permit = require_fresh_permit({"fresh": True})
    permit.consume()
    with pytest.raises(ValueError, match="already_dispatched"):
        permit.consume()


def test_physical_policy_unset_limits_are_counted_but_not_hard_evaluation_eligible():
    from app.domain.evaluation.budget import BudgetPolicy

    policy = BudgetPolicy(revision=1)
    assert policy.global_concurrency is None
    assert policy.user_concurrency is None
    assert policy.provider_concurrency is None
    assert not policy.hard_evaluation_available
    assert BudgetPolicy(
        revision=2, global_concurrency=9, user_concurrency=3, provider_concurrency=4
    ).hard_evaluation_available
    for value in (0, -1, True, "2"):
        with pytest.raises(ValueError, match="global_concurrency"):
            BudgetPolicy(revision=1, global_concurrency=value)


def test_physical_provider_kind_and_account_keys_cannot_collide():
    from app.application.evaluation.budget_service import BudgetDemandFactory
    from app.domain.evaluation.budget import BudgetPolicy
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.inference import InferenceProvider
    from app.domain.models.scope import Principal

    factory = BudgetDemandFactory(BudgetPolicy(), provider_pools={"endpoint": "kind:openai"})
    demand = factory.physical(
        AuthorizationContext.for_principal(Principal(user_id="user")),
        endpoint_id="endpoint",
        provider_kind=InferenceProvider.OPENAI,
        tokens=None,
        money=None,
    )
    keys = [bucket.key for bucket in demand.buckets]
    assert "1:provider:kind:openai" in keys
    account = next(key for key in keys if key.startswith("1:provider:account:sha256:"))
    assert account != "1:provider:kind:openai"
