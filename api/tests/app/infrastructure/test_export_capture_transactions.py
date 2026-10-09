"""Acceptance must commit only after sealing, and retries cannot reuse a transaction."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.infrastructure.repositories.db_execution_export_repository import (
    DBExecutionExportRepository,
)


@pytest.mark.asyncio
async def test_capture_seal_failure_rolls_back_acceptance():
    events = []
    repo = DBExecutionExportRepository(None, signing_secret="secret")

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        events.append("begin")
        try:
            yield object()
        except ValueError:
            events.append("rollback")
            raise
        else:
            events.append("commit")

    async def operation(db, scope, principal, operation, **payload):
        events.append(operation)
        if operation == "accept":
            return {"id": "job", "replayed": False}
        raise ValueError("export_capacity_exceeded")

    async def seal(db, scope, principal, accepted, request):
        events.append("seal")
        raise ValueError("export_capacity_exceeded")

    repo.transactions = SimpleNamespace(transaction=transaction)
    repo._operation = operation
    repo._seal = seal
    with pytest.raises(ValueError, match="export_capacity_exceeded"):
        await repo.accept("scope", "principal", {"request_id": "once"})
    assert events == ["begin", "accept", "seal", "rollback"]


@pytest.mark.asyncio
async def test_replay_never_recaptures_or_seals():
    repo = DBExecutionExportRepository(None, signing_secret="secret")

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield object()

    async def operation(db, scope, principal, operation, **payload):
        assert operation == "accept"
        return {"id": "fixed-job", "replayed": True, "status": "expired"}

    async def seal(*args):
        pytest.fail("receipt replay must not recapture")

    repo.transactions = SimpleNamespace(transaction=transaction)
    repo._operation = operation
    repo._seal = seal
    assert (await repo.accept("scope", "principal", {"request_id": "once"}))["id"] == "fixed-job"


@pytest.mark.asyncio
async def test_expired_batch_snapshot_never_falls_back_to_fresh_capture(monkeypatch):
    from unittest.mock import AsyncMock

    from app.infrastructure.repositories.db_evaluation_summary_repository import (
        DBEvaluationSummaryRepository,
    )

    get = AsyncMock(side_effect=ValueError("summary_expired"))
    capture = AsyncMock()
    monkeypatch.setattr(DBEvaluationSummaryRepository, "get", get)
    monkeypatch.setattr(DBEvaluationSummaryRepository, "capture", capture)
    repo = DBExecutionExportRepository(None, signing_secret="secret")
    with pytest.raises(ValueError, match="summary_expired"):
        await repo._seal_batch(
            object(),
            "scope",
            "principal",
            {"id": "job"},
            {
                "batch_id": "batch",
                "snapshot_id": "fixed",
                "source": "human",
                "dimension": "correctness",
                "rubric_id": "rubric",
                "evaluation_revision": 1,
            },
        )
    get.assert_awaited_once_with("scope", "principal", "batch", "fixed")
    capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_encoded_page_boundary_resumes_without_dropping_rows(monkeypatch):
    from unittest.mock import AsyncMock

    import app.infrastructure.repositories.db_execution_export_repository as module

    repo = DBExecutionExportRepository(None, signing_secret="secret")
    repo._lease = AsyncMock(
        return_value={"rows": [{"run_id": str(n), "cut": {}} for n in range(3)], "next_after": None}
    )
    monkeypatch.setattr(
        module, "run_row", lambda fixed, cut: {"label": "界" * 100000, "run_id": fixed["run_id"]}
    )
    page = await repo.page(SimpleNamespace(export_id="job"), after=-1)
    assert len(page["rows"]) == 3
    repo._lease.return_value["rows"].append({"run_id": "3", "cut": {}})
    page = await repo.page(SimpleNamespace(export_id="job"), after=-1)
    assert len(page["rows"]) == 3
    assert page["next_after"] == 2


@pytest.mark.asyncio
async def test_ack_waits_for_cleanup_delete_sweep_before_retiring_late_put(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    import app.infrastructure.repositories.db_execution_export_repository as module

    lock = asyncio.Lock()
    attempting = asyncio.Event()
    state = {"completed": False, "cleaned": False, "object": False}

    class Session:
        locked = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            if self.locked:
                lock.release()

        async def execute(self, sql, params):
            assert params["key"] == "export-object:original-intent"
            attempting.set()
            await lock.acquire()
            self.locked = True

        async def scalar(self, sql, params):
            assert self.locked
            assert params["target"] == "original-intent"
            assert params["token"] == "original-token"
            state["completed"] = True

        async def commit(self):
            pass

    repo = DBExecutionExportRepository(Session, signing_secret="secret")
    monkeypatch.setattr(module, "configure_session_authorization", AsyncMock())

    async def unprotected_kernel(*args, **kwargs):
        state["completed"] = True
        attempting.set()

    repo._kernel = unprotected_kernel
    await lock.acquire()  # Cleanup's real ownership protocol, paused after delete.
    state["object"] = False
    state["object"] = True  # SDK thread finishes its late put.
    ack = asyncio.create_task(
        repo.write_completed(
            SimpleNamespace(token="original-token"), SimpleNamespace(intent_id="original-intent")
        )
    )
    await attempting.wait()
    try:
        assert not state["completed"]
        assert not ack.done()
        state["cleaned"] = state["completed"]
    finally:
        lock.release()
        await ack
    assert state["object"]
    assert not state["cleaned"]
    assert state["completed"]
    async with lock:  # Only a later post-completion sweep can certify deletion.
        state["object"] = False
        state["cleaned"] = state["completed"]
    assert state["cleaned"]
    assert not state["object"]
