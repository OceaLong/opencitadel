"""E12 archive security through a NOBYPASS definer and ordinary scoped API UoW."""

# ruff: noqa: F401,F811
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.infrastructure.repositories.test_e06_effect_read_migration_owner import (
    fresh_f07_database,
)
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_evaluation_environment_repository import (
    environment_kernel,
)
from tests.app.integration.test_evaluation_recovery import (
    test_archive_is_idempotent_keeps_history_and_rejects_new_work as check_archive,
)

pytestmark = pytest.mark.asyncio


async def test_owner_archive_keeps_history_and_scope(budget_binding_fixture):
    await check_archive(budget_binding_fixture)


async def test_runtime_cannot_forge_archive_or_mutate_metadata(budget_binding_fixture):
    from app.application.evaluation.archive_service import ArchiveService
    from app.domain.evaluation.errors import DatasetNotFound
    from app.domain.models.authorization import AuthorizationContext

    suites, scope, principal, _, _, _, factory = budget_binding_fixture
    auth = AuthorizationContext.for_principal(principal, scope=scope, request_id="e12-forged")
    async with factory(auth) as work:
        assert not await work.db_session.scalar(
            text(
                "SELECT has_function_privilege(current_user,'public.opencitadel_e12_archive(text,text)','EXECUTE')"
            )
        )
        with pytest.raises(DBAPIError):
            async with work.db_session.begin_nested():
                await work.db_session.execute(
                    text(
                        "INSERT INTO evaluation_resource_archives(scope_key,kind,resource_id,revision,request_id,fingerprint,created_by,receipt) VALUES(:s,'dataset',:id,1,'fake','fake',:actor,'{}')"
                    ),
                    {"s": "user:" + principal.user_id, "id": uuid4(), "actor": principal.user_id},
                )
        with pytest.raises(DBAPIError):
            async with work.db_session.begin_nested():
                await work.db_session.scalar(
                    text("SELECT public.opencitadel_e12_archive('{}',:signature)"),
                    {"signature": "0" * 64},
                )
    async with suites.uow_factory(auth) as work:
        assert await work.db_session.scalar(
            text(
                "SELECT has_function_privilege(current_user,'public.opencitadel_e12_archive(text,text)','EXECUTE')"
            )
        )
    with pytest.raises(DatasetNotFound):
        await ArchiveService(suites.uow_factory).archive(
            scope,
            principal,
            kind="dataset",
            identity=uuid4(),
            expected_revision=1,
            request_id="missing",
        )


async def test_archive_busy_batch_and_archived_suite_reject_new_admission(budget_binding_fixture):
    from app.application.evaluation.archive_service import ArchiveService
    from app.domain.models.authorization import AuthorizationContext
    from tests.app.infrastructure.repositories.test_evaluation_batch_repository import (
        scheduled_batch,
    )

    suites, scope, principal, suite, _, _, factory = budget_binding_fixture
    _, _, batch = await scheduled_batch(budget_binding_fixture)
    with pytest.raises(ValueError, match="busy"):
        await ArchiveService(suites.uow_factory).archive(
            scope,
            principal,
            kind="batch",
            identity=batch.id,
            expected_revision=batch.revision,
            request_id="e12-active-batch",
        )
    draft = await suites.get_draft(scope, principal, kind="suite", entity_id=suite.entity_id)
    await ArchiveService(suites.uow_factory).archive(
        scope,
        principal,
        kind="suite",
        identity=suite.entity_id,
        expected_revision=draft.revision,
        request_id="e12-suite-archive",
    )
    async with factory(
        AuthorizationContext.for_principal(principal, scope=scope, request_id="e12-new-batch")
    ) as work:
        with pytest.raises(DBAPIError, match="evaluation_resource_archived"):
            async with work.db_session.begin_nested():
                await work.evaluation_batch.submit(
                    scope,
                    principal,
                    "start",
                    "e12-new-batch",
                    {"suite_version": str(suite.id)},
                    uuid4(),
                )
        assert (await work.evaluation_batch.get(scope, batch.id))["id"] == batch.id


async def test_archive_and_actual_actor_audit_share_transaction(
    budget_binding_fixture, monkeypatch
):
    from app.application.evaluation.archive_service import ArchiveService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_audit_repository import DBAuditRepository

    suites, scope, principal, suite, _, _, factory = budget_binding_fixture
    draft = await suites.get_draft(scope, principal, kind="suite", entity_id=suite.entity_id)
    original = DBAuditRepository.add_archive

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise ConnectionError("audit acknowledgement lost before commit")

    monkeypatch.setattr(DBAuditRepository, "add_archive", crash)
    service = ArchiveService(suites.uow_factory)
    command = {
        "kind": "suite",
        "identity": suite.entity_id,
        "expected_revision": draft.revision,
        "request_id": "e12-audit-atomic",
    }
    with pytest.raises(ConnectionError):
        await service.archive(scope, principal, **command)
    async with factory(AuthorizationContext.for_principal(principal, scope=scope)) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_resource_archives WHERE resource_id=:id"),
                {"id": suite.entity_id},
            )
            == 0
        )
    monkeypatch.setattr(DBAuditRepository, "add_archive", original)
    await service.archive(scope, principal, **command)
    async with factory(AuthorizationContext.for_principal(principal, scope=scope)) as work:
        row = (
            await work.db_session.execute(
                text("SELECT created_by FROM evaluation_resource_archives WHERE resource_id=:id"),
                {"id": suite.entity_id},
            )
        ).one()
        assert row[0] == principal.user_id


@pytest.mark.parametrize("operation", ["update_case", "from_run", "import_apply"])
async def test_archived_dataset_mutations_fail_without_changing_history(datasets, operation):
    import io
    import json

    from app.application.evaluation.archive_service import ArchiveService
    from app.domain.evaluation.dataset import CaseRevision
    from app.domain.models.authorization import AuthorizationContext
    from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import draft

    service, scope, principal, _, _ = datasets
    initial = await draft(service, scope, principal)
    preview = await service.import_validate(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=initial.revision,
        stream=io.BytesIO(
            json.dumps({"schema_version": 1, "cases": [{"case_key": "a", "input": "old"}]}).encode()
        ),
        content_type="application/json",
    )
    archive = ArchiveService(service.uow_factory)
    await archive.archive(
        scope,
        principal,
        kind="dataset",
        identity=initial.id,
        expected_revision=initial.revision,
        request_id=str(uuid4()),
    )

    async def mutate():
        if operation == "import_apply":
            await service.import_apply(
                scope,
                principal,
                dataset_id=initial.id,
                import_id=preview.import_id,
                input_digest=preview.input_digest,
                request_id=str(uuid4()),
                expected_revision=initial.revision,
            )
        else:
            await service.update_case(
                scope,
                principal,
                dataset_id=initial.id,
                request_id=str(uuid4()),
                expected_revision=initial.revision,
                case=CaseRevision(case_key="a", input="new"),
                operation=operation,
            )

    with pytest.raises((ValueError, DBAPIError), match="archived"):
        await mutate()

    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        assert (await work.evaluation_dataset.get_draft(scope, initial.id))[
            "revision"
        ] == initial.revision
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_draft_cases WHERE dataset_id=:id"),
                {"id": initial.id},
            )
            == 0
        )


async def test_archive_waits_for_concurrent_environment_allocation(datasets, environment_kernel):
    import asyncio
    from contextvars import Context

    from app.application.evaluation.archive_service import ArchiveService
    from app.domain.evaluation.environment import CaseSlot, EnvironmentLease
    from app.domain.models.authorization import AuthorizationContext
    from tests.app.infrastructure.repositories.test_evaluation_environment_repository import version

    ds, scope, principal, _, _ = datasets
    env = version()
    async with environment_kernel() as work:
        await work.evaluation_environment.register(scope, "environment", env)
        await work.commit()
    lease = EnvironmentLease(
        id=uuid4(),
        environment_version=env.id,
        case_slot=CaseSlot(
            workspace="user:" + scope.user_id,
            batch_id=uuid4(),
            case_id=uuid4(),
            config_version=uuid4(),
            repeat=1,
        ),
        generation=1,
        revision=1,
        state="allocated",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    archive = ArchiveService(ds.uow_factory)
    async with environment_kernel() as allocating:
        await allocating.evaluation_environment.allocate(scope, lease, (), concurrency=2)
        task = asyncio.create_task(
            archive.archive(
                scope,
                principal,
                kind="environment",
                identity=env.id,
                expected_revision=env.revision,
                request_id=str(uuid4()),
            ),
            context=Context(),
        )
        try:
            # The second real transaction is blocked by the allocation's archive barrier.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 0.1)
            await allocating.commit()
            with pytest.raises(ValueError, match="busy"):
                await asyncio.wait_for(task, 5)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with environment_kernel() as work:
        assert (await work.evaluation_environment.lease(scope, lease.id)).state == "allocated"
    async with ds.uow_factory(AuthorizationContext.for_principal(principal, scope=scope)) as work:
        await work.evaluation_archive.require_active(scope, "environment", env.id)


async def test_accepted_pending_environment_dependency_blocks_archive_before_first_lease(
    budget_binding_fixture,
):
    from app.application.evaluation.archive_service import ArchiveService
    from app.domain.models.authorization import AuthorizationContext
    from tests.app.infrastructure.repositories.test_evaluation_environment_repository import version

    suites, scope, principal, suite, _, _, factory = budget_binding_fixture
    env = version()
    pending_suite = suite.model_copy(
        update={
            "id": uuid4(),
            "revision": suite.revision + 1,
            "mode": "isolated",
            "environment_version": env.id,
            "recording_versions": (),
        }
    )
    batch_id = uuid4()
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        await work.evaluation_environment.register(scope, "environment", env)
        await work.evaluation_configuration.publish(scope, "suite", pending_suite)
        await work.evaluation_batch.submit(
            scope,
            principal,
            "start",
            "e12-pending-env",
            {"suite_version": str(pending_suite.id)},
            batch_id,
        )
        await work.commit()
    with pytest.raises(ValueError, match="busy"):
        await ArchiveService(suites.uow_factory).archive(
            scope,
            principal,
            kind="environment",
            identity=env.id,
            expected_revision=env.revision,
            request_id=str(uuid4()),
        )
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (await work.evaluation_batch.get(scope, batch_id))["status"] == "created"
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT count(*) FROM evaluation_environment_leases WHERE environment_version=:id"
                ),
                {"id": env.id},
            )
            == 0
        )
        assert (
            await work.evaluation_environment.registered(scope, "environment", env.id)
        ).id == env.id


async def test_archive_commit_wins_race_against_new_environment_allocation(
    datasets, environment_kernel, monkeypatch
):
    import asyncio

    from app.application.evaluation.archive_service import ArchiveService
    from app.domain.evaluation.environment import CaseSlot, EnvironmentLease
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_archive_repository import (
        DBEvaluationArchiveRepository,
    )
    from tests.app.infrastructure.repositories.test_evaluation_environment_repository import version

    ds, scope, principal, _, _ = datasets
    env = version()
    async with environment_kernel() as work:
        await work.evaluation_environment.register(scope, "environment", env)
        await work.commit()
    locked, release = asyncio.Event(), asyncio.Event()
    original = DBEvaluationArchiveRepository.archive

    async def paused(*args, **kwargs):
        receipt = await original(*args, **kwargs)
        locked.set()
        await release.wait()
        return receipt

    monkeypatch.setattr(DBEvaluationArchiveRepository, "archive", paused)
    archived = asyncio.create_task(
        ArchiveService(ds.uow_factory).archive(
            scope,
            principal,
            kind="environment",
            identity=env.id,
            expected_revision=env.revision,
            request_id=str(uuid4()),
        )
    )
    lease = EnvironmentLease(
        id=uuid4(),
        environment_version=env.id,
        case_slot=CaseSlot(
            workspace="user:" + scope.user_id,
            batch_id=uuid4(),
            case_id=uuid4(),
            config_version=uuid4(),
            repeat=1,
        ),
        generation=1,
        revision=1,
        state="allocated",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    async def allocate():
        async with environment_kernel(AuthorizationContext.system("execution-kernel")) as work:
            await work.evaluation_environment.allocate(scope, lease, (), concurrency=2)
            await work.commit()

    allocation = None
    try:
        await asyncio.wait_for(locked.wait(), 5)
        allocation = asyncio.create_task(allocate())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(allocation), 0.1)
        release.set()
        assert (await asyncio.wait_for(archived, 5))["state"] == "archived"
        with pytest.raises(DBAPIError, match="archived"):
            await asyncio.wait_for(allocation, 5)
    finally:
        release.set()
        tasks = [task for task in (archived, allocation) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    async with environment_kernel() as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_environment_leases WHERE id=:id"),
                {"id": lease.id},
            )
            == 0
        )


async def test_archived_dataset_membership_guard_and_accepted_receipt_replay(datasets):
    from app.application.evaluation.archive_service import ArchiveService
    from app.domain.evaluation.dataset import CaseRevision
    from app.domain.models.authorization import AuthorizationContext
    from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import draft

    ds, scope, principal, _, _ = datasets
    initial = await draft(ds, scope, principal)
    request = {
        "dataset_id": initial.id,
        "request_id": str(uuid4()),
        "expected_revision": 1,
        "case": CaseRevision(case_key="a", input="old"),
    }
    edited = await ds.update_case(scope, principal, **request)
    await ArchiveService(ds.uow_factory).archive(
        scope,
        principal,
        kind="dataset",
        identity=initial.id,
        expected_revision=edited.revision,
        request_id=str(uuid4()),
    )
    assert await ds.update_case(scope, principal, **request) == edited
    async with ds.uow_factory(AuthorizationContext.for_principal(principal, scope=scope)) as work:
        for statement in (
            "UPDATE evaluation_datasets SET name='hidden edit' WHERE id=:id",
            "DELETE FROM evaluation_draft_cases WHERE dataset_id=:id",
        ):
            with pytest.raises(DBAPIError, match="archived"):
                async with work.db_session.begin_nested():
                    await work.db_session.execute(text(statement), {"id": initial.id})
        assert len((await work.evaluation_dataset.get_draft(scope, initial.id))["members"]) == 1
