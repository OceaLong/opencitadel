#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.errors.exceptions import ConflictError, NotFoundError
from app.application.services.codebase_service import CodebaseService
from app.domain.models.codebase import Codebase, CodebaseStatus


class _FakeTaskState:
    def __init__(self, done: bool = True, meta: dict | None = None):
        self._done = done
        self._meta = meta or {"updated_at": 9999999999.0}
        self.request_cancel = AsyncMock()
        self.get_runtime_snapshot = AsyncMock(return_value={"is_done": True})

    async def is_done(self, _task_id: str) -> bool:
        return self._done

    async def get_task_meta(self, _task_id: str):
        return self._meta

    @staticmethod
    def heartbeat_is_stale(meta, stale_after: float) -> bool:
        if not meta:
            return True
        heartbeat = meta.get("last_heartbeat_at") or meta.get("updated_at")
        if heartbeat is None:
            return True
        import time
        return time.time() - float(heartbeat) >= stale_after


class _FakeCodebaseRepo:
    def __init__(self, codebase: Codebase | None = None):
        self._codebase = codebase
        self.deleted_ids: list[str] = []

    async def get_by_id(self, codebase_id: str, scope=None):
        if self._codebase and self._codebase.id == codebase_id:
            return self._codebase
        return None

    async def delete_by_id(self, codebase_id: str) -> None:
        self.deleted_ids.append(codebase_id)


class _FakeUow:
    def __init__(self, repo: _FakeCodebaseRepo):
        self.codebase = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSandbox:
    destroy = AsyncMock(return_value=True)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_delete_codebase_not_found():
    service = CodebaseService(
        uow_factory=lambda: _FakeUow(_FakeCodebaseRepo(None)),
        sandbox_cls=MagicMock(),
        file_storage=object(),  # type: ignore[arg-type]
    )
    with pytest.raises(NotFoundError):
        await service.delete_codebase("missing")


@pytest.mark.anyio
async def test_delete_codebase_rejects_when_ingest_running():
    codebase = Codebase(
        id="cb1",
        name="demo",
        status=CodebaseStatus.ANALYZING,
        ingest_task_id="task-1",
    )
    service = CodebaseService(
        uow_factory=lambda: _FakeUow(_FakeCodebaseRepo(codebase)),
        sandbox_cls=MagicMock(),
        file_storage=object(),  # type: ignore[arg-type]
    )
    service._task_state = _FakeTaskState(done=False)  # type: ignore[method-assign]
    with pytest.raises(ConflictError):
        await service.delete_codebase("cb1")


@pytest.mark.anyio
async def test_delete_codebase_cancels_task_and_destroys_sandbox():
    codebase = Codebase(
        id="cb1",
        name="demo",
        status=CodebaseStatus.READY,
        ingest_task_id="task-1",
        sandbox_id="sandbox-1",
    )
    repo = _FakeCodebaseRepo(codebase)
    sandbox = _FakeSandbox()
    sandbox_cls = MagicMock()
    sandbox_cls.get = AsyncMock(return_value=sandbox)
    service = CodebaseService(
        uow_factory=lambda: _FakeUow(repo),
        sandbox_cls=sandbox_cls,
        file_storage=object(),  # type: ignore[arg-type]
    )
    task_state = _FakeTaskState(done=True)
    service._task_state = task_state  # type: ignore[method-assign]

    await service.delete_codebase("cb1")

    task_state.request_cancel.assert_awaited_once_with("task-1")
    sandbox_cls.get.assert_awaited_once_with("sandbox-1")
    sandbox.destroy.assert_awaited_once()
    assert repo.deleted_ids == ["cb1"]
