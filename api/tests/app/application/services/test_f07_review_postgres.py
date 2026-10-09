import asyncio

import pytest
from sqlalchemy import text

from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.security.db_authorization import configure_session_authorization
from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_artifact_provenance_postgres import production_run
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)
from tests.app.execution_test_support import execution_admin_session

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest.mark.parametrize("lock", ["claim", "group"])
@pytest.mark.parametrize("deadline", ["claim_deadline", "timeout_at"])
async def test_physical_permit_rechecks_wall_clock_after_blocking_locks(lock, deadline):
    from app.application.execution.view_facts import attempt_key
    from app.infrastructure.repositories.db_execution_usage_repository import (
        DBExecutionUsageRepository,
    )

    _, producer, _, _ = await production_run(activity_type="model.call")
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE execution_activity_tasks SET claim_deadline=clock_timestamp()+INTERVAL '1 hour' WHERE activity_id=:id"
            ),
            {"id": producer.activity_id},
        )
        config = await DBExecutionUsageRepository(db).snapshot(
            producer.scope, producer.run_id, {}, "production"
        )
        await db.commit()
    started = asyncio.Event()

    async def allocate():
        async with execution_admin_session() as db:
            await db.execute(text("SELECT CURRENT_TIMESTAMP"))
            started.set()
            result = await DBExecutionUsageRepository(db).allocate(
                producer.scope,
                run_id=producer.run_id,
                activity_id=producer.activity_id,
                generation=0,
                claim_generation=1,
                configuration_id=config,
                request_snapshot={},
            )
            await db.commit()
            return result

    async with execution_admin_session() as blocker:
        await blocker.execute(
            text(
                f"UPDATE execution_activity_tasks SET {deadline}=clock_timestamp()+INTERVAL '400 milliseconds' WHERE activity_id=:id"
            ),
            {"id": producer.activity_id},
        )
        await blocker.commit()
        if lock == "claim":
            # The preceding commit ended the transaction-local signed RLS claim.
            # Rebind it before selecting the row to ensure FOR UPDATE locks it.
            await configure_session_authorization(
                blocker, AuthorizationContext.system("execution-test-admin")
            )
            assert (
                await blocker.scalar(
                    text("SELECT 1 FROM execution_activity_tasks WHERE activity_id=:id FOR UPDATE"),
                    {"id": producer.activity_id},
                )
                == 1
            )
        else:
            key = (
                "user:"
                + producer.scope.user_id
                + ":dispatch:"
                + attempt_key(str(producer.activity_id), 0, 1)
                + ":invoke:0"
            )
            await blocker.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key}
            )
        task = asyncio.create_task(allocate())
        await started.wait()
        await asyncio.sleep(0.5)
        assert not task.done()
        await blocker.commit()
    with pytest.raises(ValueError, match="claim unavailable"):
        await task
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text("SELECT count(*) FROM execution_model_dispatches WHERE run_id=:run"),
                {"run": producer.run_id},
            )
            == 0
        )


async def test_publication_poison_and_timeout_do_not_starve_healthy_kernel_receipts(
    isolated_database,  # noqa: F811
):
    from types import SimpleNamespace

    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_execution_usage import ExecutionUsageMaintenance
    from app.infrastructure.repositories.db_execution_usage_repository import (
        DBExecutionUsageRepository,
    )
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_kernel_database_uri

    admin, _ = isolated_database
    _, producer, _, handler = await production_run(activity_type="model.call")
    calls = []
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE execution_activity_tasks SET claim_deadline=clock_timestamp()+INTERVAL '1 hour' WHERE activity_id=:id"
            ),
            {"id": producer.activity_id},
        )
        repo = DBExecutionUsageRepository(db)
        config = await repo.snapshot(producer.scope, producer.run_id, {}, "production")
        for _ in range(4):
            calls.append(  # noqa: PERF401 - each allocation awaits the previous ordinal
                await repo.allocate(
                    producer.scope,
                    run_id=producer.run_id,
                    activity_id=producer.activity_id,
                    generation=0,
                    claim_generation=1,
                    configuration_id=config,
                    request_snapshot={},
                )
            )
        await db.commit()
    engine = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=admin.url.database)
    )
    sessions = async_sessionmaker(
        engine,
        info={
            "database_authorization_signing_secret": load_deployment_settings().database_authorization_signing_secret
        },
    )
    transient_failed = False

    async def handle(command):
        nonlocal transient_failed
        if command.payload["call_identity"] == calls[0]:
            raise ValueError("permanent local poison")
        if command.payload["call_identity"] == calls[1]:
            await asyncio.sleep(10)
        if command.payload["call_identity"] == calls[2] and not transient_failed:
            transient_failed = True
            raise ConnectionError("transient local error")
        return await handler.handle(command)

    try:
        maintenance = ExecutionUsageMaintenance(
            session_factory=sessions,
            authorization=AuthorizationContext.system("f07-review"),
            handler=SimpleNamespace(handle=handle),
            receipt_timeout=0.5,
        )
        assert (await maintenance.process_pending())["emitted"] == 1
        from app.infrastructure.security.db_authorization import configure_session_authorization

        async with sessions() as db:
            assert not await db.scalar(
                text("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user")
            )
            assert await db.scalar(text("SELECT count(*) FROM execution_usage_delivery")) == 0
            await configure_session_authorization(db, AuthorizationContext.system("f07-review"))
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM execution_usage_delivery WHERE failures=1")
                )
                == 3
            )
            assert await db.scalar(text("SELECT count(*) FROM execution_usage_publications")) == 1
        for _ in range(4):
            async with sessions() as db:
                await configure_session_authorization(db, AuthorizationContext.system("f07-review"))
                await db.execute(
                    text("UPDATE execution_usage_delivery SET next_attempt_at=clock_timestamp()")
                )
                await db.commit()
            await maintenance.process_pending()
        async with sessions() as db:
            await configure_session_authorization(db, AuthorizationContext.system("f07-review"))
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM execution_usage_delivery WHERE quarantined AND failures=5"
                    )
                )
                == 2
            )
            assert await db.scalar(text("SELECT count(*) FROM execution_usage_publications")) == 2
            assert await db.scalar(text("SELECT count(*) FROM execution_model_dispatches")) == 4
    finally:
        await engine.dispose()


async def test_durable_admission_anchor_survives_price_changes_and_concurrent_retry():
    from app.infrastructure.repositories.db_execution_usage_repository import (
        DBExecutionUsageRepository,
    )

    _, producer, _, _ = await production_run(activity_type="model.call")

    async def admit(price):
        async with execution_admin_session() as db:
            identity = await DBExecutionUsageRepository(db).admission_snapshot(
                producer.scope,
                producer.run_id,
                {"stage": "admission", "price": price},
                "production",
            )
            await db.commit()
            return identity

    original = await admit("original")
    assert await asyncio.gather(admit("changed"), admit("changed-again")) == [original, original]
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text("SELECT count(*) FROM execution_configurations WHERE run_id=:run"),
                {"run": producer.run_id},
            )
            == 1
        )
        assert (
            await DBExecutionUsageRepository(db).load_snapshot(
                producer.scope, producer.run_id, original, "production"
            )
        )["price"] == "original"
