# ruff: noqa: F401,F811
"""E05 capacity derives from real E04 lifecycle facts in the caller UoW."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.app.infrastructure.repositories.test_evaluation_environment_repository import (
    datasets,
    environment_kernel,
    fresh_f07_database,
    isolated_database,
    version,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


def lease_for(scope, principal):
    from app.domain.evaluation.environment import CaseSlot, EnvironmentLease

    workspace = "team:" + scope.team_id if scope.team_id else "user:" + scope.user_id
    return EnvironmentLease(
        id=uuid4(),
        environment_version=uuid4(),
        case_slot=CaseSlot(
            workspace=workspace, batch_id=uuid4(), case_id=uuid4(), config_version=uuid4(), repeat=1
        ),
        generation=1,
        revision=1,
        state="allocated",
        requester=principal.model_dump(mode="json"),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


async def test_global_environment_limit_counts_other_workspace_and_preserves_held_on_activation(
    datasets, environment_kernel
):
    from app.domain.evaluation.environment_capacity import EnvironmentCapacityPolicy
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.repositories.db_environment_capacity_repository import (
        DBEnvironmentCapacityRepository,
    )

    _, scope, principal, _, _ = datasets
    policy = EnvironmentCapacityPolicy(revision=1, global_limit=1)
    first = lease_for(scope, principal)
    async with environment_kernel() as work:
        repo = DBEnvironmentCapacityRepository(work.db_session)
        await repo.bootstrap(policy)
        await work.evaluation_environment.allocate(
            scope, first, (), concurrency=2, capacity_policy=policy
        )
        await work.commit()
    from app.domain.models.team import TeamRole
    from tests.app.execution_test_support import execution_admin_session

    other = OwnerScope.team(principal.user_id, str(uuid4()))
    async with execution_admin_session() as db:
        await db.execute(text("INSERT INTO teams(id,name) VALUES(:id,:id)"), {"id": other.team_id})
        await db.execute(
            text("INSERT INTO team_members(team_id,user_id,role) VALUES(:team,:user,'member')"),
            {"team": other.team_id, "user": principal.user_id},
        )
        await db.commit()
    other_principal = principal.model_copy(update={"team_roles": {other.team_id: TeamRole.MEMBER}})
    async with environment_kernel() as work:
        with pytest.raises(ValueError, match="environment_global_capacity_exhausted"):
            await work.evaluation_environment.allocate(
                other, lease_for(other, other_principal), (), concurrency=2, capacity_policy=policy
            )
    async with environment_kernel() as work:
        await DBEnvironmentCapacityRepository(work.db_session).activate(
            EnvironmentCapacityPolicy(revision=2, global_limit=2), expected_revision=1
        )
        await work.commit()
    async with environment_kernel() as work:
        with pytest.raises(ValueError, match="environment_capacity_policy_changed"):
            await work.evaluation_environment.allocate(
                scope, lease_for(scope, principal), (), concurrency=2, capacity_policy=policy
            )
    async with environment_kernel() as work:
        await work.evaluation_environment.allocate(
            other,
            lease_for(other, other_principal),
            (),
            concurrency=2,
            capacity_policy=EnvironmentCapacityPolicy(revision=2, global_limit=2),
        )
        await work.commit()


async def test_tighter_policy_reuses_immutable_environment_version_without_republication(
    datasets, environment_kernel
):
    from types import SimpleNamespace

    from app.application.evaluation.environment_service import EnvironmentService
    from app.domain.evaluation.environment_capacity import EnvironmentCapacityPolicy
    from app.infrastructure.repositories.db_environment_capacity_repository import (
        DBEnvironmentCapacityRepository,
    )

    _, scope, principal, _, _ = datasets
    value = version()
    registry = SimpleNamespace(resolve=lambda *_: object())
    policy = EnvironmentCapacityPolicy(revision=2, workspace_limit=1)
    async with environment_kernel() as work:
        await work.evaluation_environment.register(scope, "environment", value)
        control = DBEnvironmentCapacityRepository(work.db_session)
        await control.bootstrap(EnvironmentCapacityPolicy())
        await control.activate(policy, expected_revision=1)
        await work.commit()
    service = EnvironmentService(environment_kernel, registry, ceiling=1, capacity_policy=policy)
    async with environment_kernel() as work:
        lease = await service.allocate_in_uow(
            work, scope, principal, value.id, lease_for(scope, principal).case_slot
        )
        assert lease.state == "preparing"
        await work.commit()
    async with environment_kernel() as work:
        with pytest.raises(ValueError, match="environment_workspace_capacity_exhausted"):
            await service.allocate_in_uow(
                work, scope, principal, value.id, lease_for(scope, principal).case_slot
            )


async def test_environment_policy_startup_and_operator_activation_do_not_reset_occupancy(
    datasets, environment_kernel, tmp_path
):
    from app.composition.environment_capacity import (
        activate_environment_capacity,
        initialize_environment_capacity,
    )
    from app.domain.evaluation.environment_capacity import EnvironmentCapacityPolicy
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import authenticated_session_factory

    settings = load_deployment_settings()
    sessions = authenticated_session_factory(
        environment_kernel().session_factory.kw["bind"],
        signing_secret=settings.database_authorization_signing_secret,
    )
    assert (
        await initialize_environment_capacity(settings=settings, session_factory=sessions)
        == EnvironmentCapacityPolicy()
    )
    _, scope, principal, _, _ = datasets
    async with environment_kernel() as work:
        for _ in range(2):
            await work.evaluation_environment.allocate(
                scope, lease_for(scope, principal), (), concurrency=2
            )
        await work.commit()
    policy = EnvironmentCapacityPolicy(revision=2, workspace_limit=1, global_limit=1, user_limit=1)
    path = tmp_path / "environment-policy.json"
    path.write_text(policy.model_dump_json())
    assert (
        await activate_environment_capacity(
            session_factory=sessions, path=path, expected_revision=1
        )
        == policy
    )
    with pytest.raises(ValueError, match="environment_capacity_policy_changed"):
        await initialize_environment_capacity(settings=settings, session_factory=sessions)
    revised = settings.model_copy(
        update={
            "evaluation_environment_policy_revision": 2,
            "evaluation_environment_concurrency": 1,
            "evaluation_environment_global_limit": 1,
            "evaluation_environment_user_limit": 1,
        }
    )
    assert (
        await initialize_environment_capacity(settings=revised, session_factory=sessions) == policy
    )
    assert (
        await activate_environment_capacity(
            session_factory=sessions, path=path, expected_revision=1
        )
        == policy
    )
    with pytest.raises(ValueError, match="environment_capacity_policy_changed"):
        await activate_environment_capacity(
            session_factory=sessions, path=path, expected_revision=2
        )
    async with environment_kernel() as work:
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT count(*) FROM evaluation_environment_leases WHERE state <> 'verified_clean'"
                )
            )
            == 2
        )
        with pytest.raises(ValueError, match="environment_global_capacity_exhausted"):
            await work.evaluation_environment.allocate(
                scope, lease_for(scope, principal), (), concurrency=2, capacity_policy=policy
            )


@pytest.mark.parametrize("pool", ["global", "user", "workspace"])
async def test_two_concurrent_environment_allocations_share_durable_capacity(
    datasets, environment_kernel, pool
):
    import asyncio

    from app.domain.evaluation.environment_capacity import EnvironmentCapacityPolicy
    from app.domain.models.scope import OwnerScope, Principal
    from app.domain.models.team import TeamRole
    from app.infrastructure.repositories.db_environment_capacity_repository import (
        DBEnvironmentCapacityRepository,
    )
    from tests.app.application.services.test_artifact_provenance_postgres import seed
    from tests.app.execution_test_support import execution_admin_session

    _, scope, principal, _, _ = datasets
    if pool == "global":
        second, _ = await seed()
        other, other_principal = OwnerScope.personal(second), Principal(user_id=second)
        policy = EnvironmentCapacityPolicy(global_limit=1)
    else:
        team = str(uuid4())
        async with execution_admin_session() as db:
            await db.execute(text("INSERT INTO teams(id,name) VALUES(:id,:id)"), {"id": team})
            await db.execute(
                text("INSERT INTO team_members(team_id,user_id,role) VALUES(:team,:user,'member')"),
                {"team": team, "user": principal.user_id},
            )
            await db.commit()
        other = OwnerScope.team(principal.user_id, team)
        other_principal = principal.model_copy(update={"team_roles": {team: TeamRole.MEMBER}})
        policy = (
            EnvironmentCapacityPolicy(user_limit=1)
            if pool == "user"
            else EnvironmentCapacityPolicy(workspace_limit=1)
        )
        if pool == "workspace":
            scope, principal = other, other_principal
    async with environment_kernel() as work:
        await DBEnvironmentCapacityRepository(work.db_session).bootstrap(policy)
        await work.commit()

    async def allocate(target, requester):
        async with environment_kernel() as work:
            result = await work.evaluation_environment.allocate(
                target, lease_for(target, requester), (), concurrency=2, capacity_policy=policy
            )
            await work.commit()
            return result

    tasks = [
        asyncio.create_task(allocate(target, requester))
        for target, requester in ((scope, principal), (other, other_principal))
    ]
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    errors = [result for result in results if isinstance(result, BaseException)]
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert str(errors[0]) == "environment_" + pool + "_capacity_exhausted"
    async with environment_kernel() as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_environment_leases"))
            == 1
        )


@pytest.mark.parametrize("uncertainty", ["cancel", "expired", "timeout"])
async def test_unknown_prepare_and_quarantine_absence_retain_capacity_until_exact_resolution_and_repair(
    datasets, environment_kernel, uncertainty
):
    from app.application.evaluation.environment_service import EnvironmentService, EnvironmentWorker
    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.domain.evaluation.environment_capacity import EnvironmentCapacityPolicy
    from app.domain.models.user import GlobalRole
    from app.infrastructure.repositories.db_environment_capacity_repository import (
        DBEnvironmentCapacityRepository,
    )
    from tests.app.execution_test_support import execution_admin_session

    _, scope, principal, _, _ = datasets
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.commit()
    principal = principal.model_copy(update={"global_role": GlobalRole.ADMIN})

    class Adapter:
        revision = "1"
        fixture_revisions = healthcheck_revisions = frozenset({"1"})

        def validate(self, *args):
            pass

        async def cleanup(self, *args):
            return {"resources": []}

        async def verify(self, *args):
            return {"verified": True, "resources": []}

    registry = AdapterRegistry({"test": Adapter()})
    policy = EnvironmentCapacityPolicy(global_limit=1)
    service = EnvironmentService(environment_kernel, registry, capacity_policy=policy)
    worker = EnvironmentWorker(environment_kernel, registry)
    value = version()
    async with environment_kernel() as work:
        await DBEnvironmentCapacityRepository(work.db_session).bootstrap(policy)
        await work.evaluation_environment.register(scope, "environment", value)
        first = await service.allocate_in_uow(
            work, scope, principal, value.id, lease_for(scope, principal).case_slot
        )
        original, _ = await work.evaluation_environment.claim(
            scope, (await work.evaluation_environment.pending())[0]["id"]
        )
        await work.commit()
    async with environment_kernel() as work:
        repo = work.evaluation_environment
        if uncertainty == "expired":
            assert (
                await repo.claim(scope, original.id, now=datetime.now(UTC) + timedelta(minutes=6))
                is None
            )
        elif uncertainty == "timeout":
            assert await repo.complete(scope, original, {}, error="environment_unknown_operation")
        await service.cleanup_in_uow(work, scope, first.id)
        await work.commit()

    async def drain():
        for _ in range(2):
            async with environment_kernel() as work:
                operations = await work.evaluation_environment.pending()
            for operation in operations:
                await worker.process(scope, operation["id"])

    await drain()
    async with environment_kernel() as work:
        repo = work.evaluation_environment
        assert (await repo.lease(scope, first.id)).state == "quarantine"
        assert await repo.unresolved_operations(scope, first.id)
        with pytest.raises(ValueError, match="environment_global_capacity_exhausted"):
            await service.allocate_in_uow(
                work, scope, principal, value.id, lease_for(scope, principal).case_slot
            )
    async with environment_kernel() as work:
        assert not await work.evaluation_environment.complete(scope, original, {"resources": []})
        assert (await work.evaluation_environment.lease(scope, first.id)).state == "quarantine"
        await service.cleanup_in_uow(work, scope, first.id, repair=True, principal=principal)
        await work.commit()
    await drain()
    async with environment_kernel() as work:
        repo = work.evaluation_environment
        assert (await repo.lease(scope, first.id)).state == "verified_clean"
        assert not await repo.complete(scope, original, {"resources": [{"id": "stale"}]})
        assert (await repo.lease(scope, first.id)).state == "verified_clean"
        second = await service.allocate_in_uow(
            work, scope, principal, value.id, lease_for(scope, principal).case_slot
        )
        assert second.state == "preparing"
        await work.commit()


async def test_requester_is_rechecked_after_waiting_on_environment_policy(
    datasets, environment_kernel, monkeypatch
):
    import asyncio

    from app.domain.evaluation.environment_capacity import EnvironmentCapacityPolicy
    from app.infrastructure.repositories.db_environment_capacity_repository import (
        DBEnvironmentCapacityRepository,
    )
    from tests.app.execution_test_support import execution_admin_session

    _, scope, principal, _, _ = datasets
    started, go = asyncio.Event(), asyncio.Event()
    original = DBEnvironmentCapacityRepository._require_kernel

    async def observed(repo):
        await original(repo)
        started.set()

    monkeypatch.setattr(DBEnvironmentCapacityRepository, "_require_kernel", observed)

    async def waiting():
        await go.wait()
        async with environment_kernel() as work:
            await work.evaluation_environment.allocate(
                scope, lease_for(scope, principal), (), concurrency=2
            )
            await work.commit()

    # Create the sibling before entering another UoW (ContextVar isolation).
    task = asyncio.create_task(waiting())
    try:
        async with environment_kernel() as work:
            control = DBEnvironmentCapacityRepository(work.db_session)
            await control.bootstrap(EnvironmentCapacityPolicy())
            await work.commit()
        async with environment_kernel() as work:
            await DBEnvironmentCapacityRepository(work.db_session).active(lock=True)
            go.set()
            await asyncio.wait_for(started.wait(), 3)
            async with execution_admin_session() as db:
                await db.execute(
                    text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
                    {"id": principal.user_id},
                )
                await db.commit()
            await work.commit()
        results = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
        assert isinstance(results[0], PermissionError)
        assert "principal revoked" in str(results[0])
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    async with environment_kernel() as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_environment_leases"))
            == 0
        )
