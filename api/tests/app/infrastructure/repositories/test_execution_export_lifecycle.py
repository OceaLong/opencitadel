"""Actual SQL fencing/object locks with pool size one; provider is deterministic bytes.

Collect only locally. Passing these later is not real-provider or capacity proof.
"""

# ruff: noqa: F401,F811
import asyncio
from contextlib import asynccontextmanager, contextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.services.execution_export_download import ExportDownloader
from app.application.services.execution_export_worker import ExecutionExportWorker
from app.domain.external.object_storage import BoundedObjectBytes
from app.infrastructure.repositories.db_execution_export_repository import (
    DBExecutionExportRepository,
)
from app.infrastructure.security.db_authorization import configure_sync_system_authorization
from core.config import load_deployment_settings
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.execution_test_support import execution_kernel_database_uri
from tests.app.infrastructure.repositories.test_e06_effect_read_migration_owner import (
    fresh_f07_database,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_execution_comparison_repository import make_run
from tests.app.infrastructure.repositories.test_execution_export_capture import (
    authorized_read,
    authorized_system_write,
    authorized_write,
    repository,
    request,
)

pytestmark = pytest.mark.asyncio


@contextmanager
def authorized_kernel_write(engine):
    """Mutate kernel-owned fixture state under the signed kernel policy."""
    with engine.begin() as db:
        configure_sync_system_authorization(
            db,
            actor="execution-kernel",
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        )
        yield db


class PrivateObjects:
    def __init__(self):
        self.data = {}

    async def put_bytes(self, key, data):
        self.data[key] = data

    async def get_bounded_bytes(self, key, limit):
        data = self.data[key]
        return BoundedObjectBytes(data[:limit], len(data) > limit)

    async def delete_bytes(self, key):
        self.data.pop(key, None)


@asynccontextmanager
async def kernel_repository(api):
    database = api.session_factory.kw["bind"].url.database
    assert database.startswith("test_execution_view_")
    engine = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=database),
        pool_size=1,
        max_overflow=0,
        pool_timeout=2,
    )
    try:
        yield DBExecutionExportRepository(async_sessionmaker(engine), signing_secret=api.secret)
    finally:
        await engine.dispose()


async def test_restart_reclaims_expired_lease_and_fences_old_writer(datasets, isolated_database):
    service, scope, principal, *_ = datasets
    api = repository(service)
    accepted = await api.accept(scope, principal, request(await make_run(scope)))
    async with kernel_repository(api) as kernel:
        first = await kernel.claim()
        engine, _ = isolated_database
        with authorized_kernel_write(engine) as db:
            changed = db.execute(
                text(
                    "UPDATE execution_exports SET lease_until=clock_timestamp()-interval '1 second' WHERE id=CAST(:id AS uuid)"
                ),
                {"id": accepted["id"]},
            )
            assert changed.rowcount == 1
        second = await kernel.claim()
        assert second.token != first.token
        assert second.export_id == first.export_id
        with pytest.raises(ValueError, match="export_lease_lost"):
            await kernel.begin_chunk(first, ordinal=0, size=1, digest="0" * 64)


async def test_real_worker_and_download_complete_with_one_connection(datasets):
    service, scope, principal, *_ = datasets
    api = repository(service)
    accepted = await api.accept(scope, principal, request(await make_run(scope)))
    objects = PrivateObjects()
    async with kernel_repository(api) as kernel:
        assert (
            await asyncio.wait_for(ExecutionExportWorker(kernel, objects).process_pending(), 15)
            == 1
        )
    assert (await api.get(scope, principal, accepted["id"]))["status"] == "ready"
    spool, format, size = await ExportDownloader(api, objects).prepare(
        scope, principal, accepted["id"]
    )
    try:
        assert format == "json"
        assert len(spool.read()) == size
    finally:
        spool.close()


async def test_download_revocation_after_storage_io_refuses_all_bytes(datasets, isolated_database):
    service, scope, principal, *_ = datasets
    api = repository(service)
    accepted = await api.accept(scope, principal, request(await make_run(scope)))
    objects = PrivateObjects()
    async with kernel_repository(api) as kernel:
        await ExecutionExportWorker(kernel, objects).process_pending()
    original = objects.get_bounded_bytes

    async def revoke_after_read(key, limit):
        data = await original(key, limit)
        engine, _ = isolated_database
        with authorized_system_write(engine) as db:
            changed = db.execute(
                text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
                {"id": principal.user_id},
            )
            assert changed.rowcount == 1
        return data

    objects.get_bounded_bytes = revoke_after_read
    with pytest.raises(PermissionError):
        await ExportDownloader(api, objects).prepare(scope, principal, accepted["id"])


async def test_use_lease_blocks_gc_then_receipt_survives_retirement(datasets, isolated_database):
    service, scope, principal, *_ = datasets
    api = repository(service)
    payload = request(await make_run(scope))
    accepted = await api.accept(scope, principal, payload)
    objects = PrivateObjects()
    async with kernel_repository(api) as kernel:
        await ExecutionExportWorker(kernel, objects).process_pending()
        use = await api._call(scope, principal, "acquire_use", export_id=accepted["id"])
        engine, _ = isolated_database
        with authorized_kernel_write(engine) as db:
            changed = db.execute(
                text(
                    "UPDATE execution_exports SET expires_at=clock_timestamp()-interval '1 second' WHERE id=CAST(:id AS uuid)"
                ),
                {"id": accepted["id"]},
            )
            assert changed.rowcount == 1
        assert await kernel.cleanup(objects) == 0
        assert objects.data
        await api._call(
            scope, principal, "release_use", export_id=accepted["id"], use_id=use["use_id"]
        )
        assert await kernel.cleanup(objects) > 0
        assert not objects.data
    assert (await api.accept(scope, principal, payload))["id"] == accepted["id"]
    assert await api.get(scope, principal, accepted["id"]) == {
        "id": accepted["id"],
        "status": "expired",
    }


async def test_gc_cannot_delete_during_an_inflight_chunk_write(datasets, isolated_database):
    service, scope, principal, *_ = datasets
    api = repository(service)
    accepted = await api.accept(scope, principal, request(await make_run(scope)))
    objects = PrivateObjects()
    entered, release = asyncio.Event(), asyncio.Event()
    original = objects.put_bytes

    async def blocked_put(key, data):
        entered.set()
        await release.wait()
        await original(key, data)

    objects.put_bytes = blocked_put
    async with kernel_repository(api) as writer, kernel_repository(api) as cleaner:
        task = asyncio.create_task(ExecutionExportWorker(writer, objects).process_pending())
        try:
            await asyncio.wait_for(entered.wait(), 10)
            engine, _ = isolated_database
            with authorized_kernel_write(engine) as db:
                changed = db.execute(
                    text(
                        "UPDATE execution_exports SET expires_at=clock_timestamp()-interval '1 second',status='expired',lease_until=NULL WHERE id=CAST(:id AS uuid)"
                    ),
                    {"id": accepted["id"]},
                )
                assert changed.rowcount == 1
            assert await asyncio.wait_for(cleaner.cleanup(objects), 5) == 0
        finally:
            release.set()
            await asyncio.wait_for(task, 10)
        await cleaner.cleanup(objects)
        assert not objects.data
        assert (await api.get(scope, principal, accepted["id"]))["status"] == "expired"


async def test_resource_only_revocation_during_object_io_invalidates_whole_artifact(
    datasets, isolated_database
):
    service, scope, principal, *_ = datasets
    api = repository(service)
    run_id = await make_run(scope)
    engine, _ = isolated_database
    with authorized_write(engine, scope, principal) as db:
        session = db.scalar(
            text("SELECT id FROM sessions WHERE owner_user_id=:owner LIMIT 1"),
            {"owner": scope.user_id},
        )
        assert session is not None
        changed = db.execute(
            text(
                "UPDATE execution_view_runs SET source=jsonb_build_object('entity_type','session','entity_id',CAST(:session AS text)) WHERE run_id=CAST(:run AS uuid)"
            ),
            {"session": session, "run": run_id},
        )
        assert changed.rowcount == 1
        epoch = db.scalar(text("SELECT revision FROM analysis_authority_epoch WHERE singleton"))
    accepted = await api.accept(scope, principal, request(run_id))
    objects = PrivateObjects()
    original = objects.get_bounded_bytes

    async def revoke_after_verified_io(key, limit):
        data = await original(key, limit)
        with authorized_write(engine, scope, principal) as db:
            changed = db.execute(
                text("UPDATE sessions SET deleted_at=clock_timestamp() WHERE id=:id"),
                {"id": session},
            )
            assert changed.rowcount == 1
            assert (
                db.scalar(text("SELECT revision FROM analysis_authority_epoch WHERE singleton"))
                == epoch
            )
        return data

    objects.get_bounded_bytes = revoke_after_verified_io
    async with kernel_repository(api) as kernel:
        await ExecutionExportWorker(kernel, objects).process_pending()
    assert await api.get(scope, principal, accepted["id"]) == {
        "id": accepted["id"],
        "status": "invalidated",
    }


async def test_threadpool_timeout_gc_late_put_exact_ack_and_zero_residual(
    datasets, isolated_database, monkeypatch
):
    import threading

    from starlette.concurrency import run_in_threadpool

    service, scope, principal, *_ = datasets
    api = repository(service)
    accepted = await api.accept(scope, principal, request(await make_run(scope)))
    objects, started, release = PrivateObjects(), threading.Event(), threading.Event()

    def sdk_put(key, data):
        started.set()
        release.wait(5)
        objects.data[key] = data

    async def put(key, data):
        await run_in_threadpool(sdk_put, key, data)

    objects.put_bytes = put
    monkeypatch.setattr("app.application.services.execution_export_worker.IO_TIMEOUT", 0.02)
    engine, _ = isolated_database
    async with kernel_repository(api) as kernel:
        worker = ExecutionExportWorker(kernel, objects)
        try:
            await worker.process_pending()
            assert started.is_set()
            assert worker.pending_writes == 1
            await kernel.cleanup(objects)
            with authorized_read(engine, scope, principal) as db:
                intent = db.execute(
                    text(
                        "SELECT id,lease_token,write_completed,cleaned_at FROM export_object_intents WHERE export_id=CAST(:id AS uuid)"
                    ),
                    {"id": accepted["id"]},
                ).one()
                assert not intent.write_completed
                assert intent.cleaned_at is None
                assert (
                    db.scalar(
                        text(
                            "SELECT capture_bytes FROM execution_exports WHERE id=CAST(:id AS uuid)"
                        ),
                        {"id": accepted["id"]},
                    )
                    > 0
                )
            # Completion belongs to the old intent even after job fencing changes.
            with authorized_kernel_write(engine) as db:
                changed = db.execute(
                    text(
                        "UPDATE execution_exports SET lease_token=gen_random_uuid() WHERE id=CAST(:id AS uuid)"
                    ),
                    {"id": accepted["id"]},
                )
                assert changed.rowcount == 1
            await kernel._kernel(
                "write_completed",
                target=str(intent.id),
                token="00000000-0000-0000-0000-000000000000",
            )
            with authorized_read(engine, scope, principal) as db:
                assert not db.scalar(
                    text("SELECT write_completed FROM export_object_intents WHERE id=:id"),
                    {"id": intent.id},
                )
            release.set()
            while worker.pending_writes:
                await worker.reap_writes()
                await asyncio.sleep(0.001)
            assert objects.data
            await kernel.cleanup(objects)
            assert objects.data == {}
            with authorized_read(engine, scope, principal) as db:
                assert db.scalar(
                    text("SELECT cleaned_at IS NOT NULL FROM export_object_intents WHERE id=:id"),
                    {"id": intent.id},
                )
                assert (
                    db.scalar(
                        text(
                            "SELECT capture_bytes FROM execution_exports WHERE id=CAST(:id AS uuid)"
                        ),
                        {"id": accepted["id"]},
                    )
                    == 0
                )
                assert (
                    db.scalar(
                        text(
                            "SELECT count(*) FROM export_receipts WHERE export_id=CAST(:id AS uuid)"
                        ),
                        {"id": accepted["id"]},
                    )
                    == 1
                )
        finally:
            release.set()
            await worker.close()


async def test_cleanup_paused_after_delete_cannot_retire_a_late_put(
    datasets, isolated_database, monkeypatch
):
    import threading

    from starlette.concurrency import run_in_threadpool

    service, scope, principal, *_ = datasets
    api = repository(service)
    accepted = await api.accept(scope, principal, request(await make_run(scope)))
    objects = PrivateObjects()
    release_put = threading.Event()
    deleted, resume_cleanup = asyncio.Event(), asyncio.Event()

    def sdk_put(key, data):
        release_put.wait(10)
        objects.data[key] = data

    async def put(key, data):
        await run_in_threadpool(sdk_put, key, data)

    async def delete_then_pause(key):
        objects.data.pop(key, None)
        deleted.set()
        await resume_cleanup.wait()

    objects.put_bytes, objects.delete_bytes = put, delete_then_pause
    monkeypatch.setattr("app.application.services.execution_export_worker.IO_TIMEOUT", 0.02)
    engine, _ = isolated_database
    async with kernel_repository(api) as kernel, kernel_repository(api) as cleaner:
        worker = ExecutionExportWorker(kernel, objects)
        cleanup = ack = None
        try:
            await worker.process_pending()
            assert worker.pending_writes == 1
            cleanup = asyncio.create_task(cleaner.cleanup(objects))
            await asyncio.wait_for(deleted.wait(), 5)
            release_put.set()
            await asyncio.wait_for(asyncio.gather(*worker._writes), 5)
            assert objects.data
            ack = asyncio.create_task(worker.reap_writes())
            # Observe the real PostgreSQL lock wait, not an arbitrary timing gap.
            async with asyncio.timeout(5):
                while True:
                    with engine.connect() as db:
                        waiting = db.scalar(
                            text(
                                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted AND database=(SELECT oid FROM pg_database WHERE datname=current_database()))"
                            )
                        )
                    if waiting:
                        break
                    assert not ack.done(), "ack bypassed cleanup ownership"
                    await asyncio.sleep(0.001)
            resume_cleanup.set()
            await asyncio.wait_for(asyncio.gather(cleanup, ack), 5)
            with authorized_read(engine, scope, principal) as db:
                state = db.execute(
                    text(
                        "SELECT write_completed,cleaned_at FROM export_object_intents WHERE export_id=CAST(:id AS uuid)"
                    ),
                    {"id": accepted["id"]},
                ).one()
                assert state.write_completed
                assert state.cleaned_at is None
                assert (
                    db.scalar(
                        text(
                            "SELECT capture_bytes FROM execution_exports WHERE id=CAST(:id AS uuid)"
                        ),
                        {"id": accepted["id"]},
                    )
                    > 0
                )
            assert objects.data
            await cleaner.cleanup(objects)
            assert objects.data == {}
            with authorized_read(engine, scope, principal) as db:
                assert db.scalar(
                    text(
                        "SELECT cleaned_at IS NOT NULL FROM export_object_intents WHERE export_id=CAST(:id AS uuid)"
                    ),
                    {"id": accepted["id"]},
                )
                assert (
                    db.scalar(
                        text(
                            "SELECT capture_bytes FROM execution_exports WHERE id=CAST(:id AS uuid)"
                        ),
                        {"id": accepted["id"]},
                    )
                    == 0
                )
        finally:
            release_put.set()
            resume_cleanup.set()
            for task in (cleanup, ack):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (cleanup, ack) if task is not None), return_exceptions=True
            )
            await worker.close()
