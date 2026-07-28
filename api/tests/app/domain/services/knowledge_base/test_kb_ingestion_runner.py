#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import MagicMock

import pytest

from app.domain.models.app_config import AppConfig
from app.domain.models.error_codes import DOCUMENT_PARSE_FAILED
from app.domain.models.event import DoneEvent, ErrorEvent, StepEvent, StepEventStatus
from app.domain.models.knowledge_base import DocStatus, KBStatus, KnowledgeBase, KnowledgeDocument
from app.domain.services.knowledge_base.ingestion_runner import KBIngestionRunner
from app.domain.services.knowledge_base.parsers import PageBlock


class _FakeKbRepo:
    def __init__(self, kb: KnowledgeBase, documents: list | None = None):
        self._kb = kb
        self._documents = documents or []
        self.status_updates: list[tuple[str, KBStatus, str | None]] = []
        self.doc_updates: list[tuple] = []

    async def get_kb(self, kb_id: str, scope=None):
        return self._kb if self._kb.id == kb_id else None

    async def list_documents(self, kb_id: str):
        return self._kb.id == kb_id and self._documents or []

    async def update_status(self, kb_id: str, status: KBStatus, error: str | None = None):
        self.status_updates.append((kb_id, status, error))
        self._kb.status = status
        self._kb.error = error

    async def update_document_status(
            self,
            doc_id: str,
            status,
            error: str | None = None,
            warning: str | None = None,
            page_count: int | None = None,
    ):
        self.doc_updates.append((doc_id, status, error, warning, page_count))
        for doc in self._documents:
            if doc.id == doc_id:
                doc.status = status
                doc.error = error
                doc.warning = warning
                if page_count is not None:
                    doc.page_count = page_count

    async def replace_index_chunks(self, kb_id: str, chunks):
        self.replaced_chunks = chunks

    async def save_chunks(self, chunks):
        self.saved_chunks = getattr(self, "saved_chunks", [])
        self.saved_chunks.extend(chunks)

    async def purge_documents_index_data(self, doc_ids):
        self.purged_doc_ids = getattr(self, "purged_doc_ids", [])
        self.purged_doc_ids.extend(doc_ids)

    async def count_child_chunks(self, kb_id):
        return len([c for c in getattr(self, "saved_chunks", []) if c.level.value == "child"])

    async def save_kb(self, kb: KnowledgeBase) -> None:
        self._kb = kb


class _FakeUow:
    def __init__(self, kb_repo: _FakeKbRepo):
        self.knowledge_base = kb_repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _collect_events(runner: KBIngestionRunner, kb_id: str):
    events = []
    async for event in runner.run(kb_id):
        events.append(event)
    return events


@pytest.mark.anyio
async def test_run_yields_valid_parse_step_event(monkeypatch):
    kb = KnowledgeBase(id="kb1", name="test")
    kb_repo = _FakeKbRepo(kb)
    runner = KBIngestionRunner(uow_factory=lambda: _FakeUow(kb_repo), file_storage=MagicMock())
    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner.get_runtime_config",
        lambda: AppConfig(),
    )

    events = await _collect_events(runner, "kb1")

    assert isinstance(events[0], StepEvent)
    assert events[0].step.id == "parse"
    assert events[0].step.description == "正在解析文档..."
    assert events[0].status == StepEventStatus.STARTED


@pytest.mark.anyio
async def test_run_fails_when_no_documents(monkeypatch):
    kb = KnowledgeBase(id="kb1", name="test")
    kb_repo = _FakeKbRepo(kb)
    runner = KBIngestionRunner(uow_factory=lambda: _FakeUow(kb_repo), file_storage=MagicMock())
    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner.get_runtime_config",
        lambda: AppConfig(),
    )

    events = await _collect_events(runner, "kb1")

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].error == "知识库没有待解析文档"
    assert events[-1].code == DOCUMENT_PARSE_FAILED
    assert kb.status == KBStatus.FAILED
    assert any(status == KBStatus.PARSING for _, status, _ in kb_repo.status_updates)
    assert any(status == KBStatus.FAILED for _, status, _ in kb_repo.status_updates)


@pytest.mark.anyio
async def test_empty_document_content_fails_at_parse_stage(monkeypatch):
    from io import BytesIO
    from unittest.mock import AsyncMock

    from app.domain.models.file import File
    from app.domain.models.knowledge_base import DocStatus, KBSourceType, KnowledgeDocument
    from app.domain.services.knowledge_base.parsers import ParseResult

    doc = KnowledgeDocument(
        id="doc1",
        kb_id="kb1",
        title="empty.pdf",
        source_type=KBSourceType.UPLOAD,
        source_ref="{}",
        file_id="file-1",
        mime="application/pdf",
    )
    kb = KnowledgeBase(id="kb1", name="test")
    kb_repo = _FakeKbRepo(kb, [doc])
    file_storage = MagicMock()
    file_storage.download_file = AsyncMock(
        return_value=(
            BytesIO(b"%PDF-1.4"),
            File(id="file-1", filename="empty.pdf", mime_type="application/pdf", size=9),
        ),
    )
    runner = KBIngestionRunner(uow_factory=lambda: _FakeUow(kb_repo), file_storage=file_storage)
    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner.get_runtime_config",
        lambda: AppConfig(),
    )

    async def _empty_parse(*_args, **_kwargs):
        return ParseResult(blocks=[], page_count=0, warning="primary empty")

    async def _empty_ocr(*_args, **_kwargs):
        return [], "OCR 未执行：无法渲染 PDF 页面图像"

    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner.parse_document",
        _empty_parse,
    )
    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner.ocr_pdf_to_blocks",
        _empty_ocr,
    )

    events = await _collect_events(runner, "kb1")

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].error.startswith("全部新增文档解析失败:")
    assert "OCR 未执行" in events[-1].error
    assert kb_repo.doc_updates
    assert kb_repo.doc_updates[-1][1] == DocStatus.FAILED
    assert "OCR 未执行" in (kb_repo.doc_updates[-1][2] or "")


@pytest.mark.anyio
async def test_run_degrades_when_embedding_fails(monkeypatch):
    from io import BytesIO
    from unittest.mock import AsyncMock

    from app.domain.models.file import File
    from app.domain.models.knowledge_base import DocStatus, KBSourceType, KBStatus, KnowledgeDocument
    from app.domain.services.knowledge_base.parsers import PageBlock, ParseResult

    doc = KnowledgeDocument(
        id="doc1",
        kb_id="kb1",
        title="sample.pdf",
        source_type=KBSourceType.UPLOAD,
        source_ref="{}",
        file_id="file-1",
        mime="application/pdf",
    )
    kb = KnowledgeBase(id="kb1", name="test")
    kb_repo = _FakeKbRepo(kb, [doc])
    file_storage = MagicMock()
    file_storage.download_file = AsyncMock(
        return_value=(
            BytesIO(b"%PDF-1.4 sample"),
            File(id="file-1", filename="sample.pdf", mime_type="application/pdf", size=16),
        ),
    )
    runner = KBIngestionRunner(uow_factory=lambda: _FakeUow(kb_repo), file_storage=file_storage)

    cfg = AppConfig()
    cfg.knowledge_base.vector_enabled = True
    cfg.feature_flags.enable_embeddings = True
    cfg.knowledge_base.graphrag.enabled = False
    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner.get_runtime_config",
        lambda: cfg,
    )

    async def _parse_with_text(*_args, **_kwargs):
        return ParseResult(
            blocks=[PageBlock(page_no=1, heading_path="sample.pdf", text="hello world " * 20)],
            page_count=1,
            warning=None,
        )

    async def _fail_embed(_self, _contents):
        raise TimeoutError("Request timed out")

    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner.parse_document",
        _parse_with_text,
    )
    monkeypatch.setattr(
        "app.domain.services.knowledge_base.vector_service.KBVectorService.embed_batch",
        _fail_embed,
    )

    events = await _collect_events(runner, "kb1")

    assert isinstance(events[-1], DoneEvent)
    assert kb.status == KBStatus.READY
    assert kb.vector_degraded is True
    assert getattr(kb_repo, "saved_chunks", None)
    assert all(not chunk.embedding for chunk in kb_repo.saved_chunks)
    assert any(
        update[1] == DocStatus.READY and update[3] == "向量化失败，已降级为 BM25 检索"
        for update in kb_repo.doc_updates
    )


def _make_doc(doc_id: str, status: DocStatus) -> KnowledgeDocument:
    return KnowledgeDocument(id=doc_id, kb_id="kb1", title=doc_id, status=status)


@pytest.mark.anyio
async def test_run_processes_only_pending_and_failed_documents(monkeypatch):
    kb = KnowledgeBase(id="kb1", name="test")
    docs = [
        _make_doc("d-ready", DocStatus.READY),
        _make_doc("d-pending", DocStatus.PENDING),
        _make_doc("d-failed", DocStatus.FAILED),
    ]
    kb_repo = _FakeKbRepo(kb, documents=docs)
    runner = KBIngestionRunner(uow_factory=lambda: _FakeUow(kb_repo), file_storage=MagicMock())
    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner.get_runtime_config",
        lambda: AppConfig(),
    )
    parsed_ids = []

    async def fake_parse(self, doc):
        parsed_ids.append(doc.id)
        return [PageBlock(page_no=1, heading_path="h", text="hello world")], 1, None

    monkeypatch.setattr(KBIngestionRunner, "_parse_document", fake_parse)

    events = await _collect_events(runner, "kb1")

    assert sorted(parsed_ids) == ["d-failed", "d-pending"]          # ready 文档未被重新解析
    assert sorted(kb_repo.purged_doc_ids) == ["d-failed", "d-pending"]  # 先清自身残留
    assert not hasattr(kb_repo, "replaced_chunks")                   # 不再全量替换
    assert kb.status == KBStatus.READY
    assert any(isinstance(ev, DoneEvent) for ev in events)


@pytest.mark.anyio
async def test_run_short_circuits_when_no_pending_documents(monkeypatch):
    kb = KnowledgeBase(id="kb1", name="test")
    kb_repo = _FakeKbRepo(kb, documents=[_make_doc("d-ready", DocStatus.READY)])
    runner = KBIngestionRunner(uow_factory=lambda: _FakeUow(kb_repo), file_storage=MagicMock())
    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner.get_runtime_config",
        lambda: AppConfig(),
    )

    events = await _collect_events(runner, "kb1")

    assert kb.status == KBStatus.READY
    assert any(isinstance(ev, DoneEvent) for ev in events)
    assert not any(isinstance(ev, ErrorEvent) for ev in events)


@pytest.mark.anyio
async def test_run_keeps_kb_ready_when_new_doc_fails_but_ready_docs_exist(monkeypatch):
    kb = KnowledgeBase(id="kb1", name="test")
    docs = [_make_doc("d-ready", DocStatus.READY), _make_doc("d-new", DocStatus.PENDING)]
    kb_repo = _FakeKbRepo(kb, documents=docs)
    runner = KBIngestionRunner(uow_factory=lambda: _FakeUow(kb_repo), file_storage=MagicMock())
    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner.get_runtime_config",
        lambda: AppConfig(),
    )

    async def failing_parse(self, doc):
        raise ValueError("boom")

    monkeypatch.setattr(KBIngestionRunner, "_parse_document", failing_parse)

    events = await _collect_events(runner, "kb1")

    assert kb.status == KBStatus.READY          # 库不因新文档失败而 failed
    assert kb.error and "1" in kb.error          # 错误摘要
    doc_failed = [u for u in kb_repo.doc_updates if u[0] == "d-new" and u[1] == DocStatus.FAILED]
    assert doc_failed
    assert any(isinstance(ev, DoneEvent) for ev in events)


@pytest.mark.anyio
async def test_run_fails_kb_when_all_docs_fail_and_none_ready(monkeypatch):
    kb = KnowledgeBase(id="kb1", name="test")
    kb_repo = _FakeKbRepo(kb, documents=[_make_doc("d-new", DocStatus.PENDING)])
    runner = KBIngestionRunner(uow_factory=lambda: _FakeUow(kb_repo), file_storage=MagicMock())
    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner.get_runtime_config",
        lambda: AppConfig(),
    )

    async def failing_parse(self, doc):
        raise ValueError("boom")

    monkeypatch.setattr(KBIngestionRunner, "_parse_document", failing_parse)

    events = await _collect_events(runner, "kb1")

    assert kb.status == KBStatus.FAILED
    assert any(isinstance(ev, ErrorEvent) for ev in events)
