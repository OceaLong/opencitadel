#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock

import pytest

from app.application.errors.exceptions import BadRequestError, ConflictError, NotFoundError
from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.domain.models.knowledge_base import KBStatus, KnowledgeBase
from app.infrastructure.external.task.task_state import TaskStatus


class _FakeTaskState:
    def __init__(self, done: bool = True, meta: dict | None = None):
        self._done = done
        self._meta = meta or {"updated_at": 9999999999.0}
        self.set_status_calls: list[tuple[str, object]] = []
        self.register_calls: list[tuple] = []
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

    async def set_status(self, task_id: str, status) -> None:
        self.set_status_calls.append((task_id, status))

    async def register_task(self, task_id, session_id, task_type="kb_ingest", resource_id="", request_id=""):
        self.register_calls.append((task_id, session_id, task_type, resource_id, request_id))


class _FakeKbSaveRepo:
    def __init__(self):
        self.saved: list[KnowledgeBase] = []

    async def save_kb(self, kb: KnowledgeBase) -> None:
        self.saved.append(kb)


class _FakeUowWithKb:
    knowledge_base = None
    session = None

    def __init__(self, kb_repo):
        self.knowledge_base = kb_repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_add_documents_rejects_empty_payload():
    service = KnowledgeBaseService(uow_factory=lambda: _FakeUowWithKb(_FakeKbSaveRepo()), file_storage=object())  # type: ignore[arg-type]
    service.get_kb = lambda kb_id, scope=None: _async_kb(kb_id)  # type: ignore[method-assign]
    with pytest.raises(BadRequestError):
        await service.add_documents("kb1")


@pytest.mark.anyio
async def test_reindex_is_idempotent_when_ingest_running():
    service = KnowledgeBaseService(uow_factory=lambda: _FakeUowWithKb(_FakeKbSaveRepo()), file_storage=object())  # type: ignore[arg-type]
    kb = KnowledgeBase(id="kb1", name="test", status=KBStatus.PARSING, ingest_task_id="task-1")
    service.get_kb = lambda kb_id, scope=None: _async_kb_obj(kb)  # type: ignore[method-assign]
    service._task_state = _FakeTaskState(done=False)  # type: ignore[method-assign]
    result = await service.reindex("kb1")
    assert result.ingest_task_id == "task-1"


@pytest.mark.anyio
async def test_reindex_restarts_when_kb_failed_with_stale_task(monkeypatch):
    kb_repo = _FakeKbSaveRepo()
    service = KnowledgeBaseService(uow_factory=lambda: _FakeUowWithKb(kb_repo), file_storage=object())  # type: ignore[arg-type]
    kb = KnowledgeBase(
        id="kb1",
        name="test",
        status=KBStatus.FAILED,
        ingest_task_id="task-old",
        error="全部文档解析失败",
    )
    service.get_kb = lambda kb_id, scope=None: _async_kb_obj(kb)  # type: ignore[method-assign]
    task_state = _FakeTaskState(done=False)
    service._task_state = task_state  # type: ignore[method-assign]

    dispatched: list[str] = []

    class _FakeTask:
        def __init__(self, task_id: str, session_id: str):
            self.task_id = task_id
            self.session_id = session_id

        async def dispatch_to_worker(self) -> None:
            dispatched.append(self.task_id)

    monkeypatch.setattr(
        "app.application.services.knowledge_base_service.RedisStreamTask",
        lambda task_id, session_id: _FakeTask(task_id, session_id),
    )

    result = await service.reindex("kb1")

    assert result.ingest_task_id != "task-old"
    assert result.status == KBStatus.PENDING
    assert result.error is None
    assert dispatched == [result.ingest_task_id]
    assert task_state.set_status_calls == [("task-old", TaskStatus.FAILED)]
    assert kb_repo.saved


@pytest.mark.anyio
async def test_add_documents_rejects_when_ingest_running():
    service = KnowledgeBaseService(uow_factory=lambda: _FakeUowWithKb(_FakeKbSaveRepo()), file_storage=object())  # type: ignore[arg-type]
    kb = KnowledgeBase(id="kb1", name="test", status=KBStatus.PARSING, ingest_task_id="task-1")
    service.get_kb = lambda kb_id, scope=None: _async_kb_obj(kb)  # type: ignore[method-assign]
    service._task_state = _FakeTaskState(done=False)  # type: ignore[method-assign]
    with pytest.raises(ConflictError):
        await service.add_documents("kb1", file_ids=["file-1"])


async def _async_kb(kb_id: str) -> KnowledgeBase:
    return KnowledgeBase(id=kb_id, name="kb")


async def _async_kb_obj(kb: KnowledgeBase) -> KnowledgeBase:
    return kb


class _FakeKbRepo:
    def __init__(self, doc):
        self._doc = doc

    async def get_document(self, doc_id: str):
        return self._doc if self._doc and self._doc.id == doc_id else None

    async def list_chunks_for_document(self, doc_id: str, page_no=None, limit=30):
        return []


@pytest.mark.anyio
async def test_read_document_rejects_cross_kb_access():
    from app.domain.models.knowledge_base import KnowledgeDocument, KBSourceType

    doc = KnowledgeDocument(
        id="doc-1",
        kb_id="kb-a",
        title="secret",
        source_type=KBSourceType.UPLOAD,
        source_ref="x",
    )
    uow = _FakeUowWithKb(_FakeKbRepo(doc))
    service = KnowledgeBaseService(uow_factory=lambda: uow, file_storage=object())  # type: ignore[arg-type]
    with pytest.raises(NotFoundError):
        await service.read_document("doc-1", kb_id="kb-b")


class _DeleteKbRepo(_FakeKbSaveRepo):
    def __init__(self, kb: KnowledgeBase):
        super().__init__()
        self._kb = kb
        self.deleted_kb_ids: list[str] = []
        self.deleted_doc_ids: list[str] = []
        self.cleared_kb_ids: list[str] = []
        self._documents: list = []
        self._remaining_after_delete = 0

    async def get_kb(self, kb_id: str, scope=None):
        return self._kb if self._kb.id == kb_id else None

    async def delete_kb(self, kb_id: str) -> None:
        self.deleted_kb_ids.append(kb_id)

    async def get_document(self, doc_id: str):
        for doc in self._documents:
            if doc.id == doc_id:
                return doc
        return None

    async def delete_document(self, doc_id: str) -> None:
        self.deleted_doc_ids.append(doc_id)

    async def count_documents(self, kb_id: str) -> int:
        return self._remaining_after_delete

    async def clear_index_data(self, kb_id: str) -> None:
        self.cleared_kb_ids.append(kb_id)


@pytest.mark.anyio
async def test_delete_kb_success():
    kb = KnowledgeBase(id="kb1", name="test", status=KBStatus.READY, ingest_task_id="task-old")
    repo = _DeleteKbRepo(kb)
    service = KnowledgeBaseService(uow_factory=lambda: _FakeUowWithKb(repo), file_storage=object())  # type: ignore[arg-type]
    service.get_kb = lambda kb_id, scope=None: _async_kb_obj(kb)  # type: ignore[method-assign]
    task_state = _FakeTaskState(done=True)
    service._task_state = task_state  # type: ignore[method-assign]

    await service.delete_kb("kb1")

    assert repo.deleted_kb_ids == ["kb1"]
    task_state.request_cancel.assert_awaited_once_with("task-old")


@pytest.mark.anyio
async def test_delete_kb_rejects_when_ingest_running():
    kb = KnowledgeBase(id="kb1", name="test", status=KBStatus.PARSING, ingest_task_id="task-1")
    service = KnowledgeBaseService(uow_factory=lambda: _FakeUowWithKb(_DeleteKbRepo(kb)), file_storage=object())  # type: ignore[arg-type]
    service.get_kb = lambda kb_id, scope=None: _async_kb_obj(kb)  # type: ignore[method-assign]
    service._task_state = _FakeTaskState(done=False)  # type: ignore[method-assign]
    with pytest.raises(ConflictError):
        await service.delete_kb("kb1")


@pytest.mark.anyio
async def test_delete_document_clears_index_when_last_document(monkeypatch):
    from app.domain.models.knowledge_base import KnowledgeDocument, KBSourceType

    kb = KnowledgeBase(id="kb1", name="test", status=KBStatus.READY, doc_count=1, chunk_count=3)
    repo = _DeleteKbRepo(kb)
    doc = KnowledgeDocument(
        id="doc-1",
        kb_id="kb1",
        title="doc",
        source_type=KBSourceType.UPLOAD,
        source_ref="x",
    )
    repo._documents = [doc]
    repo._remaining_after_delete = 0
    service = KnowledgeBaseService(uow_factory=lambda: _FakeUowWithKb(repo), file_storage=object())  # type: ignore[arg-type]
    service.get_kb = lambda kb_id, scope=None: _async_kb_obj(kb)  # type: ignore[method-assign]
    service._task_state = _FakeTaskState(done=True)  # type: ignore[method-assign]

    reindex_called = False

    async def _fake_reindex(kb_id, scope=None):
        nonlocal reindex_called
        reindex_called = True
        return kb

    monkeypatch.setattr(service, "reindex", _fake_reindex)

    result = await service.delete_document("kb1", "doc-1")

    assert repo.deleted_doc_ids == ["doc-1"]
    assert repo.cleared_kb_ids == ["kb1"]
    assert result.doc_count == 0
    assert result.chunk_count == 0
    assert result.status == KBStatus.PENDING
    assert reindex_called is False


@pytest.mark.anyio
async def test_delete_document_reindexes_when_documents_remain(monkeypatch):
    from app.domain.models.knowledge_base import KnowledgeDocument, KBSourceType

    kb = KnowledgeBase(id="kb1", name="test", status=KBStatus.READY, doc_count=2, chunk_count=5)
    repo = _DeleteKbRepo(kb)
    doc = KnowledgeDocument(
        id="doc-1",
        kb_id="kb1",
        title="doc",
        source_type=KBSourceType.UPLOAD,
        source_ref="x",
    )
    repo._documents = [doc]
    repo._remaining_after_delete = 1
    service = KnowledgeBaseService(uow_factory=lambda: _FakeUowWithKb(repo), file_storage=object())  # type: ignore[arg-type]
    service.get_kb = lambda kb_id, scope=None: _async_kb_obj(kb)  # type: ignore[method-assign]
    service._task_state = _FakeTaskState(done=True)  # type: ignore[method-assign]

    reindex_called = False

    async def _fake_reindex(kb_id, scope=None):
        nonlocal reindex_called
        reindex_called = True
        kb.doc_count = 1
        return kb

    monkeypatch.setattr(service, "reindex", _fake_reindex)

    result = await service.delete_document("kb1", "doc-1")

    assert repo.deleted_doc_ids == ["doc-1"]
    assert repo.cleared_kb_ids == []
    assert reindex_called is True
    assert result.doc_count == 1
