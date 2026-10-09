import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.ports.execution_export import ExportChunk, ExportLease
from app.application.services.execution_export_worker import ExecutionExportWorker
from app.domain.external.object_storage import BoundedObjectBytes
from app.domain.models.scope import OwnerScope, Principal


@pytest.fixture
def worker():
    calls = []
    objects = {}
    lease = ExportLease(
        "job",
        "lease",
        OwnerScope.personal("u"),
        Principal(user_id="u"),
        datetime.now(UTC) + timedelta(hours=1),
    )
    repo = SimpleNamespace(
        claim=AsyncMock(return_value=lease),
        renew=AsyncMock(),
        current=AsyncMock(),
        header=AsyncMock(
            return_value={
                "format": "json",
                "metadata": {"table_kind": "runs", "data_row_count": 1},
                "metrics": {},
                "columns": [{"name": "label", "kind": "text"}],
            }
        ),
        page=AsyncMock(return_value={"rows": [{"label": "=value"}], "next_after": None}),
        publish=AsyncMock(),
        write_completed=AsyncMock(),
        fail=AsyncMock(),
    )

    async def begin(lease, *, ordinal, size, digest):
        calls.append("intent")
        return ExportChunk(str(ordinal), "private/" + str(ordinal), ordinal, size, digest)

    async def put(key, data):
        assert calls[-1] == "intent"
        calls.append("put")
        objects[key] = data

    async def read(key, limit):
        calls.append("read")
        assert limit <= 1024 * 1024
        return BoundedObjectBytes(objects[key], False)

    @asynccontextmanager
    async def chunk_io(*args):
        yield

    repo.chunk_io = chunk_io
    repo.begin_chunk = AsyncMock(side_effect=begin)
    storage = SimpleNamespace(
        put_bytes=AsyncMock(side_effect=put), get_bounded_bytes=AsyncMock(side_effect=read)
    )
    return ExecutionExportWorker(repo, storage), repo, storage, objects, calls


@pytest.mark.asyncio
async def test_worker_intent_before_io_and_verified_manifest_before_publish(worker):
    job, repo, _, objects, calls = worker
    assert await job.process_pending() == 1
    assert calls == ["intent", "put", "read"]
    assert repo.publish.await_count == 1
    chunks = repo.publish.await_args.args[1]
    body = b"".join(objects[c.key] for c in chunks)
    assert repo.publish.await_args.kwargs["digest"] == hashlib.sha256(body).hexdigest()
    assert repo.publish.await_args.kwargs["size"] == len(body)
    assert repo.current.await_count >= 2


@pytest.mark.asyncio
async def test_worker_corrupt_provider_never_publishes(worker):
    job, repo, storage, _, _ = worker
    storage.get_bounded_bytes.side_effect = None
    storage.get_bounded_bytes.return_value = BoundedObjectBytes(b"corrupt", False)
    await job.process_pending()
    repo.publish.assert_not_awaited()
    repo.fail.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_revocation_after_object_io_never_publishes(worker):
    job, repo, _, _, _ = worker
    repo.current.side_effect = [None, PermissionError("revoked")]
    await job.process_pending()
    repo.publish.assert_not_awaited()
    repo.fail.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_pages_and_splits_large_file_into_bounded_immutable_chunks(worker):
    job, repo, _, objects, _ = worker
    repo.header.return_value["metadata"]["data_row_count"] = 300
    repo.page.side_effect = [
        {"rows": [{"label": "x" * 4096} for _ in range(200)], "next_after": 199},
        {"rows": [{"label": "x" * 4096} for _ in range(100)], "next_after": None},
    ]
    await job.process_pending()
    repo.fail.assert_not_awaited()
    assert len(objects) == 2
    assert all(0 < len(part) <= 1024 * 1024 for part in objects.values())
    assert [call.kwargs["after"] for call in repo.page.await_args_list] == [-1, 199]
    assert [call.kwargs["ordinal"] for call in repo.begin_chunk.await_args_list] == [0, 1]


@pytest.mark.asyncio
async def test_cancelled_worker_leaves_durable_claim_reclaimable(worker):
    import asyncio

    job, repo, storage, _, _ = worker
    storage.put_bytes.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await job.process_pending()
    repo.publish.assert_not_awaited()
    repo.fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_truncation_never_publishes_even_if_prefix_digest_matches(worker):
    job, repo, storage, objects, _ = worker

    async def truncated(key, limit):
        return BoundedObjectBytes(objects[key], True)

    storage.get_bounded_bytes.side_effect = truncated
    await job.process_pending()
    repo.publish.assert_not_awaited()
    repo.fail.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_threadpool_late_put_remains_owned_until_positive_completion(
    worker, monkeypatch, cancel
):
    import asyncio
    import threading

    from starlette.concurrency import run_in_threadpool

    job, repo, storage, objects, _ = worker
    started, release = threading.Event(), threading.Event()
    completed = set()
    repo.write_completed = AsyncMock(side_effect=lambda lease, chunk: completed.add(chunk.key))
    monkeypatch.setattr(
        "app.application.services.execution_export_worker.IO_TIMEOUT", 0.02, raising=False
    )

    def sdk_put(key, data):
        started.set()
        release.wait(5)
        objects[key] = data

    async def put(key, data):
        await run_in_threadpool(sdk_put, key, data)

    storage.put_bytes.side_effect = put
    task = asyncio.create_task(job.process_pending())
    try:
        await asyncio.to_thread(started.wait, 1)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await asyncio.wait_for(task, 0.2)
        assert not completed
        # Cleanup may observe absence but must keep the unacknowledged intent.
        objects.clear()
        assert job.pending_writes == 1
        release.set()
        while job.pending_writes:
            await job.reap_writes()
            await asyncio.sleep(0.001)
        assert completed == set(objects)
        assert objects  # The real SDK thread did put after the first sweep.
        objects.clear()  # Subsequent eligible cleanup, now with positive completion.
        assert not objects
        repo.publish.assert_not_awaited()
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_positive_completion_ack_failure_is_retried_and_registry_is_bounded(
    worker, monkeypatch
):
    import asyncio

    job, repo, _, _, _ = worker
    completed = asyncio.create_task(asyncio.sleep(0))
    await completed
    chunk = ExportChunk("original-intent", "original-key", 0, 1, "0" * 64)
    job._writes[completed] = (repo.claim.return_value, chunk)
    repo.write_completed.side_effect = [RuntimeError("database unavailable"), None]
    with pytest.raises(RuntimeError, match="database unavailable"):
        await job.reap_writes()
    assert job.pending_writes == 1
    await job.reap_writes()
    assert job.pending_writes == 0
    assert repo.write_completed.await_args.args[1] == chunk
    blocker = asyncio.Event()
    tasks = [asyncio.create_task(blocker.wait()) for _ in range(5)]
    for task in tasks:
        job._writes[task] = (repo.claim.return_value, chunk)
    assert await job.process_pending() == 0
    repo.claim.assert_not_awaited()
    await asyncio.wait_for(job.close(), 0.2)
    assert all(task.done() for task in tasks)
    assert repo.write_completed.await_count == 2
