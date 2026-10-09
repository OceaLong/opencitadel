# ruff: noqa: F811
import asyncio
from uuid import uuid4

import pytest

from app.domain.evaluation.budget import BudgetDemand, BudgetSettlement
from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import (
    datasets,  # noqa: F401
)
from tests.app.infrastructure.repositories.test_evaluation_environment_repository import (
    environment_kernel,  # noqa: F401
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


def demand(scope, requester, *, batch, tokens=60, slots=10):
    return BudgetDemand.model_validate(
        {
            "principal": {
                "user_id": requester.user_id,
                "token_version": requester.token_version,
                "global_role": requester.global_role.value,
            },
            "scope": "user:" + scope.user_id,
            "requester": requester.user_id,
            "purpose": "evaluation_subject",
            "batch_id": batch,
            "tokens": tokens,
            "money": None,
            "buckets": [
                {"key": "0:global", "slots": slots},
                {"key": "5:batch:" + batch, "tokens": 100},
            ],
        }
    )


def repository(uow):
    from app.infrastructure.repositories.db_evaluation_budget_repository import (
        DBEvaluationBudgetRepository,
    )
    from core.config import load_deployment_settings

    return DBEvaluationBudgetRepository(
        uow.db_session,
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )


async def test_two_workers_atomically_compete_for_remaining_batch_budget(
    datasets, environment_kernel
):
    _, scope, principal, _, _ = datasets
    value = demand(scope, principal, batch=str(uuid4()))

    async def reserve():
        async with environment_kernel() as uow:
            try:
                result = await repository(uow).reserve(str(uuid4()), value)
                await uow.commit()
                return result["fresh"]
            except ValueError as exc:
                assert "budget_exhausted" in str(exc)  # noqa: PT017 - two racing outcomes
                return False

    outcomes = await asyncio.gather(reserve(), reserve(), return_exceptions=True)
    assert not any(isinstance(value, BaseException) for value in outcomes), outcomes
    assert sorted(outcomes) == [False, True]


async def test_unknown_preserves_tokens_and_slots_late_usage_is_exactly_once(
    datasets, environment_kernel
):
    _, scope, principal, _, _ = datasets
    value = demand(scope, principal, batch=str(uuid4()), slots=1)
    identity = str(uuid4())
    async with environment_kernel() as uow:
        repo = repository(uow)
        assert (await repo.reserve(identity, value))["fresh"]
        assert not (await repo.reserve(identity, value))["fresh"]
        await repo.mark_unknown(identity, value)
        await uow.commit()
    async with environment_kernel() as uow:
        with pytest.raises(ValueError, match="concurrency_exhausted"):
            await repository(uow).reserve(str(uuid4()), value)
    async with environment_kernel() as uow:
        repo = repository(uow)
        fact = BudgetSettlement(tokens=10, evidence="late-original-response")
        assert (await repo.settle(identity, value, fact))["fresh"]
        assert not (await repo.settle(identity, value, fact))["fresh"]
        await uow.commit()
    async with environment_kernel() as uow:
        assert (await repository(uow).reserve(str(uuid4()), value))["fresh"]
        await uow.commit()


async def test_conflicting_identity_or_late_evidence_never_overwrites(datasets, environment_kernel):
    _, scope, principal, _, _ = datasets
    value = demand(scope, principal, batch=str(uuid4()))
    identity = str(uuid4())
    async with environment_kernel() as uow:
        await repository(uow).reserve(identity, value)
        await uow.commit()
    async with environment_kernel() as uow:
        with pytest.raises(ValueError, match="identity_conflict"):
            await repository(uow).reserve(identity, value.model_copy(update={"tokens": 20}))
    async with environment_kernel() as uow:
        await repository(uow).settle(identity, value, BudgetSettlement(tokens=10))
        await uow.commit()
    async with environment_kernel() as uow:
        with pytest.raises(ValueError, match="settlement_conflict"):
            await repository(uow).settle(identity, value, BudgetSettlement(tokens=9))


async def test_api_role_requires_both_scoped_authorization_and_unexpired_operation(datasets):
    import json

    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from app.domain.models.authorization import AuthorizationContext

    ds, scope, principal, _, _ = datasets
    value = demand(scope, principal, batch=str(uuid4()))
    identity = str(uuid4())
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    async with ds.uow_factory(auth) as uow:
        repo = repository(uow)
        original, signature = await repo._envelope("reserve", identity, value)
        changed = json.loads(original)
        changed["demand"]["tokens"] = 0
        with pytest.raises(ValueError, match="envelope_invalid"):
            await repo.apply(json.dumps(changed), signature)
    async with ds.uow_factory(auth) as uow:
        repo = repository(uow)
        with pytest.raises(ValueError, match="envelope_expired"):
            await repo.apply(*await repo._envelope("reserve", identity, value, expires=1))
    async with ds.uow_factory(auth) as uow:
        repo = repository(uow)
        with pytest.raises(ValueError, match="scope_"):
            await repo.reserve(identity, value.model_copy(update={"scope": "user:foreign"}))
    async with ds.uow_factory(auth) as uow:
        repo = repository(uow)
        assert (await repo.reserve(identity, value))["fresh"]
        await uow.commit()
    async with ds.uow_factory(auth) as uow:
        assert not (await repository(uow).reserve(identity, value))["fresh"]
        await uow.commit()
    async with ds.uow_factory(auth) as uow:
        with pytest.raises(DBAPIError):
            await uow.db_session.execute(text("SELECT * FROM evaluation_budget_buckets"))
    async with ds.uow_factory(auth) as uow:
        with pytest.raises(DBAPIError):
            await uow.db_session.execute(text("DELETE FROM evaluation_budget_reservations"))


async def test_actual_bound_breach_records_full_usage_and_blocks_future_dispatch(
    datasets, environment_kernel
):
    from sqlalchemy import text

    _, scope, principal, _, _ = datasets
    value = demand(scope, principal, batch=str(uuid4()))
    identity = str(uuid4())
    async with environment_kernel() as uow:
        repo = repository(uow)
        await repo.reserve(identity, value)
        await repo.settle(identity, value, BudgetSettlement(tokens=1000))
        await uow.commit()
    async with environment_kernel() as uow:
        assert (
            await uow.db_session.scalar(
                text("SELECT spent_tokens FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 1000
        )
        with pytest.raises(ValueError, match="bound_breached"):
            await repository(uow).reserve(str(uuid4()), value)


async def test_revoked_requester_cannot_reserve_and_replayed_auth_cannot_rebind(datasets):
    from sqlalchemy import text

    from app.domain.models.authorization import AuthorizationContext
    from tests.app.execution_test_support import execution_admin_session

    ds, scope, principal, _, _ = datasets
    auth = AuthorizationContext.for_principal(principal, scope=scope, request_id="original")
    value = demand(scope, principal, batch=str(uuid4()))
    async with ds.uow_factory(auth) as uow:
        repo = repository(uow)
        envelope = await repo._envelope("reserve", str(uuid4()), value)
    async with ds.uow_factory(auth.model_copy(update={"request_id": "different-request"})) as uow:
        with pytest.raises(ValueError, match="authorization_binding"):
            await repository(uow).apply(*envelope)
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": principal.user_id},
        )
        await db.commit()
    async with ds.uow_factory(auth) as uow:
        with pytest.raises(ValueError, match="requester_revoked"):
            await repository(uow).reserve(str(uuid4()), value)


@pytest.mark.parametrize("signature", [None, "", "0" * 64])
async def test_missing_or_forged_operation_mac_never_grants_capacity(datasets, signature):
    from app.domain.models.authorization import AuthorizationContext

    ds, scope, principal, _, _ = datasets
    async with ds.uow_factory(AuthorizationContext.for_principal(principal, scope=scope)) as uow:
        repo = repository(uow)
        encoded, _ = await repo._envelope(
            "reserve", str(uuid4()), demand(scope, principal, batch=str(uuid4()))
        )
        with pytest.raises(ValueError, match="envelope_invalid"):
            await repo.apply(encoded, signature)


async def test_base_auth_forgery_and_operation_substitution_are_denied(datasets):
    import json

    from sqlalchemy import text

    from app.domain.models.authorization import AuthorizationContext

    ds, scope, principal, _, _ = datasets
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    value = demand(scope, principal, batch=str(uuid4()))
    async with ds.uow_factory(auth) as uow:
        repo = repository(uow)
        encoded, signature = await repo._envelope("reserve", str(uuid4()), value)
        changed = json.loads(encoded)
        changed["operation"] = "unknown"
        with pytest.raises(ValueError, match="envelope_invalid"):
            await repo.apply(json.dumps(changed), signature)
    async with ds.uow_factory(auth) as uow:
        repo = repository(uow)
        encoded, signature = await repo._envelope("reserve", str(uuid4()), value)
        await uow.db_session.execute(text("SELECT set_config('app.is_admin','true',true)"))
        with pytest.raises(ValueError, match="authorization_invalid"):
            await repo.apply(encoded, signature)


@pytest.mark.parametrize("operation", ["reserve", "lock"])
async def test_expiry_is_rechecked_after_waiting_on_capacity_lock(
    datasets, environment_kernel, operation
):
    import time

    _, scope, principal, _, _ = datasets
    value = demand(scope, principal, batch=str(uuid4()), tokens=40)
    entering, start = asyncio.Event(), asyncio.Event()

    async def wait_for_lock():
        await start.wait()
        async with environment_kernel() as uow:
            repo = repository(uow)
            encoded, signature = await repo._envelope(
                operation, str(uuid4()), value, expires=time.time() + 0.1
            )
            entering.set()
            with pytest.raises(ValueError, match="envelope_expired"):
                await repo.apply(encoded, signature)

    task = asyncio.create_task(wait_for_lock())
    try:
        async with environment_kernel() as uow:
            await repository(uow).reserve(str(uuid4()), value)
            start.set()
            await asyncio.wait_for(entering.wait(), 2)
            await asyncio.sleep(0.2)
            await uow.commit()
        await asyncio.wait_for(task, 2)
    finally:
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_two_scopes_share_one_global_pool_and_all_tasks_finish(datasets, environment_kernel):
    from app.domain.models.scope import OwnerScope, Principal
    from tests.app.application.services.test_artifact_provenance_postgres import seed

    _, scope, principal, _, _ = datasets
    foreign, _ = await seed()
    pairs = [(scope, principal), (OwnerScope.personal(foreign), Principal(user_id=foreign))]
    values = [demand(s, p, batch=str(uuid4()), slots=1) for s, p in pairs]

    async def reserve(value):
        async with environment_kernel() as uow:
            try:
                result = await repository(uow).reserve(str(uuid4()), value)
                await uow.commit()
                return result["fresh"]
            except ValueError as exc:
                if "concurrency_exhausted" not in str(exc):
                    raise
                return False

    outcomes = await asyncio.wait_for(
        asyncio.gather(*(reserve(v) for v in values), return_exceptions=True), 5
    )
    assert not any(isinstance(v, BaseException) for v in outcomes), outcomes
    assert sorted(outcomes) == [False, True]


async def test_team_role_change_rejects_new_reserve_but_kernel_records_original_late_usage(
    datasets, environment_kernel
):
    from sqlalchemy import text

    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope
    from app.domain.models.team import TeamRole
    from tests.app.execution_test_support import execution_admin_session

    ds, _, principal, _, _ = datasets
    team = str(uuid4())
    principal = principal.model_copy(update={"team_roles": {team: TeamRole.MEMBER}})
    scope = OwnerScope.team(principal.user_id, team)
    async with execution_admin_session() as db:
        await db.execute(text("INSERT INTO teams(id,name) VALUES(:id,:id)"), {"id": team})
        await db.execute(
            text("INSERT INTO team_members(team_id,user_id,role) VALUES(:team,:user,'member')"),
            {"team": team, "user": principal.user_id},
        )
        await db.commit()
    value = demand(OwnerScope.personal(principal.user_id), principal, batch=str(uuid4()))
    raw = value.model_dump()
    raw["scope"] = "team:" + team
    raw["principal"]["team_role"] = "member"
    value = BudgetDemand.model_validate(raw)
    identity = str(uuid4())
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    async with ds.uow_factory(auth) as uow:
        await repository(uow).reserve(identity, value)
        await uow.commit()
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE team_members SET role='admin' WHERE team_id=:team AND user_id=:user"),
            {"team": team, "user": principal.user_id},
        )
        await db.commit()
    async with ds.uow_factory(auth) as uow:
        with pytest.raises(ValueError, match="membership_revoked"):
            await repository(uow).reserve(str(uuid4()), value)
    async with environment_kernel() as uow:
        assert (await repository(uow).settle(identity, value, BudgetSettlement(tokens=10)))["fresh"]
        await uow.commit()
    async with environment_kernel() as uow:
        assert (await repository(uow).mark_unknown(identity, value))["state"] == "settled"


async def test_current_principal_proof_cannot_upgrade_old_signed_role(datasets):
    from sqlalchemy import text

    from app.domain.models.authorization import AuthorizationContext
    from tests.app.execution_test_support import execution_admin_session

    ds, scope, principal, _, _ = datasets
    value = demand(scope, principal, batch=str(uuid4()))
    raw = value.model_dump()
    raw["principal"]["global_role"] = "admin"
    value = BudgetDemand.model_validate(raw)
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.commit()
    async with ds.uow_factory(AuthorizationContext.for_principal(principal, scope=scope)) as uow:
        with pytest.raises(ValueError, match="role_binding"):
            await repository(uow).reserve(str(uuid4()), value)


@pytest.mark.parametrize("registered", [True, False])
async def test_endpoint_aliases_cannot_escape_one_provider_pool(
    datasets, environment_kernel, registered
):
    from app.application.evaluation.budget_service import BudgetDemandFactory
    from app.domain.evaluation.budget import BudgetPolicy
    from app.domain.models.authorization import AuthorizationContext

    ds, scope, principal, _, _ = datasets
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    factory = BudgetDemandFactory(
        BudgetPolicy(global_concurrency=7, user_concurrency=3, provider_concurrency=1),
        provider_pools={"alias-a": "one-account", "alias-b": "one-account"} if registered else {},
    )
    async with environment_kernel() as work:
        await work.evaluation_physical_policy.bootstrap(factory.policy)
        await work.commit()
    first = factory.physical(auth, endpoint_id="alias-a", tokens=None, money=None)
    second = factory.physical(auth, endpoint_id="alias-b", tokens=None, money=None)
    async with ds.uow_factory(auth) as uow:
        await repository(uow).reserve(str(uuid4()), first)
        await uow.commit()
    async with ds.uow_factory(auth) as uow:
        with pytest.raises(ValueError, match="concurrency_exhausted"):
            await repository(uow).reserve(str(uuid4()), second)


async def test_lock_only_authority_cannot_mint_permit_or_substitute_reservation(
    datasets, environment_kernel
):
    from sqlalchemy import text

    from app.application.evaluation.budget_service import require_fresh_permit

    _, scope, principal, _, _ = datasets
    value = demand(scope, principal, batch=str(uuid4()), tokens=40)
    async with environment_kernel() as work:
        repo = repository(work)
        body, signature = await repo._envelope("lock", str(uuid4()), value)
        result = await repo.apply(body, signature)
        assert result == {"state": "locked"}
        with pytest.raises(ValueError, match="already_dispatched"):
            require_fresh_permit(result)
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_budget_reservations")
            )
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 0
        )
        await work.commit()
    async with environment_kernel() as work:
        with pytest.raises(ValueError, match="envelope_invalid"):
            await repository(work).apply(
                body.replace('"operation":"lock"', '"operation":"reserve"'), signature
            )
