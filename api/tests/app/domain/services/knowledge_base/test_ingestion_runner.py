#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import MagicMock

import pytest

from app.domain.models.app_config import AppConfig
from app.domain.models.error_codes import DOCUMENT_PARSE_FAILED
from app.domain.models.event import ErrorEvent, StepEvent, StepEventStatus
from app.domain.models.knowledge_base import KBStatus, KnowledgeBase
from app.domain.services.knowledge_base.ingestion_runner import KBIngestionRunner


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
    assert events[-1].error.startswith("全部文档解析失败:")
    assert "OCR 未执行" in events[-1].error
    assert kb_repo.doc_updates
    assert kb_repo.doc_updates[-1][1] == DocStatus.FAILED
    assert "OCR 未执行" in (kb_repo.doc_updates[-1][2] or "")
