# ruff: noqa: F401,F811 -- actual PostgreSQL fixture dependencies
"""Kill a real Worker after summary receipt commit, then recover from durable bytes."""

import asyncio
import hashlib
import json
import multiprocessing
import os
import signal
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.domain.external.object_storage import BoundedObjectBytes, ObjectNotFoundError
from tests.app.infrastructure.repositories.test_context_physical_dispatch import (
    budget_binding_fixture,
    compact_runtime,
    configurations,
    datasets,
    fresh_f07_database,
    isolated_database,
    object_sessions,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


class DurableObjects:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir()

    def path(self, key):
        return self.directory / hashlib.sha256(key.encode()).hexdigest()

    async def put_bytes(self, key, body):
        target = self.path(key)
        temporary = target.with_suffix(".pending")
        with temporary.open("wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)

    async def get_bytes(self, key):
        try:
            return self.path(key).read_bytes()
        except FileNotFoundError as error:
            raise ObjectNotFoundError(key) from error

    async def get_bounded_bytes(self, key, limit):
        try:
            with self.path(key).open("rb") as stream:
                body = stream.read(limit + 1)
        except FileNotFoundError as error:
            raise ObjectNotFoundError(key) from error
        return BoundedObjectBytes(data=body[:limit], truncated=len(body) > limit)


@pytest.mark.skipif(os.name != "posix", reason="real SIGKILL acceptance requires POSIX")
async def test_a06_sigkill_after_summary_commit_recovers_without_another_provider_call(
    compact_runtime, tmp_path
):
    r = compact_runtime
    durable = DurableObjects(tmp_path / "objects")
    for key, body in r.objects._storage.objects.items():
        await durable.put_bytes(key, body)
    r.objects._storage = durable
    committed = tmp_path / "committed.json"
    execute = r.compact.execute

    async def crash_after_commit(request, context):
        outcome = await execute(request, context)
        assert outcome.status == "succeeded"
        assert await r.compact.recovery_safe(request, context.run)
        with committed.open("w") as stream:
            json.dump({"result_ref": outcome.result_ref, "sends": len(r.sends)}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        # This kills the process running ActivityWorker before it can submit
        # CompleteActivity; no exception/retry handler can run in that process.
        os.kill(os.getpid(), signal.SIGKILL)

    def child_worker():
        async def run():
            # A fork must never reuse the parent's asyncpg sockets or event loop.
            # Replace the inherited pool without closing the parent's sockets.
            await r.uow().session_factory.kw["bind"].dispose(close=False)
            r.compact.execute = crash_after_commit
            await r.worker.run_once(now=datetime.now(UTC), limit=1)

        asyncio.run(run())

    child = multiprocessing.get_context("fork").Process(target=child_worker)
    child.start()
    try:
        await asyncio.to_thread(child.join, 60)
        assert not child.is_alive(), "summary Worker did not reach the committed fault point"
        assert child.exitcode == -signal.SIGKILL
        receipt = json.loads(committed.read_text())
        assert receipt["sends"] == 1
        assert await durable.get_bytes(receipt["result_ref"])
        async with r.uow(r.auth) as work:
            assert (
                await work.db_session.scalar(
                    text("SELECT count(*) FROM execution_context_summary_proposals")
                )
                == 1
            )
            assert (
                await work.db_session.scalar(
                    text("SELECT count(*) FROM execution_model_dispatches")
                )
                == 1
            )
            assert (
                await work.db_session.scalar(
                    text("SELECT count(*) FROM execution_model_settlements")
                )
                == 1
            )
            assert (
                await work.db_session.scalar(
                    text(
                        "SELECT count(*) FROM execution_events WHERE event_type='ActivityCompleted' AND public_payload->>'activity_id'=:id"
                    ),
                    {"id": str(r.activity_id)},
                )
                == 0
            )
            await work.db_session.execute(
                text(
                    "UPDATE execution_activity_tasks SET claim_deadline=clock_timestamp()-INTERVAL '1 second' WHERE activity_id=:id"
                ),
                {"id": r.activity_id},
            )
            await work.commit()
        assert (await r.worker.run_once(now=datetime.now(UTC), limit=1)).succeeded == 1
        assert r.sends == [], "the replacement Worker must recover without a native resend"
        async with r.uow(r.auth) as work:
            assert (
                await work.db_session.scalar(
                    text("SELECT count(*) FROM execution_model_dispatches")
                )
                == 1
            )
            assert (
                await work.db_session.scalar(
                    text(
                        "SELECT count(*) FROM execution_events WHERE event_type='ActivityCompleted' AND public_payload->>'activity_id'=:id"
                    ),
                    {"id": str(r.activity_id)},
                )
                == 1
            )
    finally:
        if child.is_alive():
            child.kill()
            await asyncio.to_thread(child.join, 5)
        child.close()
