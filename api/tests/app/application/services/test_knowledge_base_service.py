#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.errors.exceptions import BadRequestError, ConflictError, NotFoundError
from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.domain.models.knowledge_base import KBStatus, KnowledgeBase
from app.domain.models.scope import OwnerScope
from app.infrastructure.external.task.task_state import TaskStatus


class _FakeTaskState:
    def __init__(self, done: bool = True, meta: dict | None = None):
        self._done = done
        self._meta = meta or {"updated_at": 9999999999.0}
        self.set_status_calls: list[tuple[str, int, object]] = []
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

    async def set_status(
            self,
            task_id: str,
            run_generation: int,
            status,
    ) -> bool:
        self.set_status_calls.append((task_id, run_generation, status))
        return True

    async def register_task(self, task_id, session_id, task_type="kb_ingest", resource_id="", request_id=""):
        self.register_calls.append((task_id, session_id, task_type, resource_id, request_id))


class _FakeKbSaveRepo:
    def __init__(self):
        self.saved: list[KnowledgeBase] = []
        self.saved_documents: list = []

    async def save_kb(self, kb: KnowledgeBase) -> None:
        self.saved.append(kb)

    async def save_document(self, document) -> None:
        self.saved_documents.append(document)

    async def mark_documents_pending(self, kb_id: str) -> None:
        pass

    async def clear_index_data(self, kb_id: str) -> None:
        pass


class _FakeUowWithKb:
    knowledge_base = None
    session = None

    def __init__(self, kb_repo, file_repo=None):
        self.knowledge_base = kb_repo
        self.file = file_repo

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


class _UnauthorizedFileRepo:
    async def get_by_id(self, file_id: str, scope=None):
        return None


class _VictimFileStorage:
    async def download_file(self, file_id: str):
        from io import BytesIO
        from app.domain.models.file import File

        return (
            BytesIO(b"victim tenant secret"),
            File(
                id=file_id,
                filename="secret.txt",
                mime_type="text/plain",
                owner_user_id="victim-user",
            ),
        )


@pytest.mark.anyio
async def test_add_documents_rejects_file_outside_owner_scope(monkeypatch):
    kb_repo = _FakeKbSaveRepo()
    uow = _FakeUowWithKb(kb_repo, file_repo=_UnauthorizedFileRepo())
    service = KnowledgeBaseService(
        uow_factory=lambda: uow,
        file_storage=_VictimFileStorage(),
    )
    kb = KnowledgeBase(id="kb1", name="test", status=KBStatus.READY)
    monkeypatch.setattr(
        service,
        "get_kb",
        lambda kb_id, scope=None: _async_kb_obj(kb),
    )

    with pytest.raises(BadRequestError, match="不存在或无权访问"):
        await service.add_documents(
            "kb1",
            file_ids=["victim-file"],
            scope=OwnerScope.personal("attacker-user"),
        )

    assert kb_repo.saved_documents == []


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
        self._ready_count = 0
        self._chunk_count_after_delete = 0

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

    async def count_ready_documents(self, kb_ids):
        return {kb_id: self._ready_count for kb_id in kb_ids}

    async def count_child_chunks(self, kb_id: str) -> int:
        return self._chunk_count_after_delete


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


# ---------------------------------------------------------------------------
# Task 6 新增用例: 增量派发 / 精确同步删除 / ready_doc_count / 分页
# 下方 _Inc* 前缀的 fixture 与本文件顶部的同名 fixture（_FakeTaskState 等）刻意区分命名，
# 避免模块级重名遮蔽，导致上面已有用例在运行时解析到不兼容的类定义。
# ---------------------------------------------------------------------------
from app.domain.models.knowledge_base import DocStatus, KnowledgeDocument as _IncKnowledgeDocument


class _IncFakeKbRepo:
    def __init__(self, kb: KnowledgeBase, documents: list | None = None):
        self.kb = kb
        self.documents = documents or []
        self.cleared = False
        self.marked_pending = False
        self.deleted_doc_ids: list[str] = []

    async def get_kb(self, kb_id, scope=None):
        return self.kb if self.kb.id == kb_id else None

    async def save_kb(self, kb):
        self.kb = kb

    async def save_document(self, doc):
        self.documents.append(doc)

    async def list_documents(self, kb_id):
        return self.documents

    async def list_documents_page(self, kb_id, limit=50, offset=0):
        return self.documents[offset:offset + limit], len(self.documents)

    async def get_document(self, doc_id):
        return next((d for d in self.documents if d.id == doc_id), None)

    async def delete_document(self, doc_id):
        self.deleted_doc_ids.append(doc_id)
        self.documents = [d for d in self.documents if d.id != doc_id]

    async def count_documents(self, kb_id):
        return len(self.documents)

    async def count_ready_documents(self, kb_ids):
        return {kb_id: len([d for d in self.documents if d.status == DocStatus.READY]) for kb_id in kb_ids}

    async def count_child_chunks(self, kb_id):
        return 42

    async def clear_index_data(self, kb_id):
        self.cleared = True

    async def mark_documents_pending(self, kb_id):
        self.marked_pending = True
        for doc in self.documents:
            doc.status = DocStatus.PENDING

    async def list_kbs(self, limit=100, offset=0, scope=None):
        return [self.kb]


class _IncFakeUow:
    def __init__(self, repo):
        self.knowledge_base = repo
        self.file = AsyncMock()      # await uow.file.get_by_id(...)
        self.session = AsyncMock()   # await uow.session.save(...)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _IncFakeTaskState:
    async def register_task(self, *args, **kwargs):
        pass

    async def get_task_meta(self, task_id):
        return None

    async def is_done(self, task_id):
        return True

    def heartbeat_is_stale(self, meta, seconds):
        return True

    async def set_status(self, *args, **kwargs):
        pass


def _inc_service(monkeypatch, repo):
    monkeypatch.setattr(
        "app.application.services.knowledge_base_service.get_task_state",
        lambda: _IncFakeTaskState(),
    )
    # URL 校验可能做 DNS/SSRF 检查，测试中直接放行
    monkeypatch.setattr(
        "app.application.services.knowledge_base_service.validate_public_url",
        lambda url: url,
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(
        "app.application.services.knowledge_base_service.RedisStreamTask.dispatch_to_worker",
        dispatch,
        raising=True,
    )
    service = KnowledgeBaseService(uow_factory=lambda: _IncFakeUow(repo), file_storage=MagicMock())
    return service, dispatch


def _inc_doc(doc_id, status=DocStatus.READY):
    return _IncKnowledgeDocument(id=doc_id, kb_id="kb1", title=doc_id, status=status)


@pytest.mark.anyio
async def test_list_kbs_populates_ready_doc_count(monkeypatch):
    repo = _IncFakeKbRepo(
        KnowledgeBase(id="kb1", name="t", status=KBStatus.READY),
        [_inc_doc("d1"), _inc_doc("d2", status=DocStatus.FAILED)],
    )
    service, _ = _inc_service(monkeypatch, repo)

    kbs = await service.list_kbs()

    assert kbs[0].ready_doc_count == 1


@pytest.mark.anyio
async def test_create_session_allowed_when_any_ready_doc(monkeypatch):
    kb = KnowledgeBase(id="kb1", name="t", status=KBStatus.PARSING)  # 摄取中
    repo = _IncFakeKbRepo(kb, [_inc_doc("d1")])
    service, _ = _inc_service(monkeypatch, repo)

    session = await service.create_session_for_kb("kb1")

    assert session.knowledge_base_id == "kb1"


@pytest.mark.anyio
async def test_create_session_rejected_when_no_ready_doc(monkeypatch):
    repo = _IncFakeKbRepo(KnowledgeBase(id="kb1", name="t", status=KBStatus.PENDING), [])
    service, _ = _inc_service(monkeypatch, repo)

    with pytest.raises(BadRequestError):
        await service.create_session_for_kb("kb1")


@pytest.mark.anyio
async def test_list_documents_paginates(monkeypatch):
    repo = _IncFakeKbRepo(KnowledgeBase(id="kb1", name="t"), [_inc_doc(f"d{i}") for i in range(7)])
    service, _ = _inc_service(monkeypatch, repo)

    docs, total = await service.list_documents("kb1", limit=5, offset=5)

    assert total == 7
    assert [d.id for d in docs] == ["d5", "d6"]
