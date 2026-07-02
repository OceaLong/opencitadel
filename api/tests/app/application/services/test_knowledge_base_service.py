#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.errors.exceptions import BadRequestError, ConflictError, NotFoundError
from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.domain.models.knowledge_base import KBStatus, KnowledgeBase


class _FakeTaskState:
    def __init__(self, done: bool = True):
        self._done = done

    async def is_done(self, _task_id: str) -> bool:
        return self._done


class _FakeUow:
    knowledge_base = None
    session = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_add_documents_rejects_empty_payload():
    service = KnowledgeBaseService(uow_factory=lambda: _FakeUow(), file_storage=object())  # type: ignore[arg-type]
    service.get_kb = lambda kb_id, scope=None: _async_kb(kb_id)  # type: ignore[method-assign]
    with pytest.raises(BadRequestError):
        await service.add_documents("kb1")


@pytest.mark.anyio
async def test_reindex_is_idempotent_when_ingest_running():
    service = KnowledgeBaseService(uow_factory=lambda: _FakeUow(), file_storage=object())  # type: ignore[arg-type]
    kb = KnowledgeBase(id="kb1", name="test", ingest_task_id="task-1")
    service.get_kb = lambda kb_id, scope=None: _async_kb_obj(kb)  # type: ignore[method-assign]
    service._task_state = _FakeTaskState(done=False)  # type: ignore[method-assign]
    result = await service.reindex("kb1")
    assert result.ingest_task_id == "task-1"


@pytest.mark.anyio
async def test_add_documents_rejects_when_ingest_running():
    service = KnowledgeBaseService(uow_factory=lambda: _FakeUow(), file_storage=object())  # type: ignore[arg-type]
    kb = KnowledgeBase(id="kb1", name="test", ingest_task_id="task-1")
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
    uow = _FakeUow()
    uow.knowledge_base = _FakeKbRepo(doc)  # type: ignore[assignment]
    service = KnowledgeBaseService(uow_factory=lambda: uow, file_storage=object())  # type: ignore[arg-type]
    with pytest.raises(NotFoundError):
        await service.read_document("doc-1", kb_id="kb-b")
