"""Parser boundaries used by the candidate-only ingestion pipeline."""

from __future__ import annotations

import inspect
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.file import File
from app.domain.models.knowledge_base import (
    KBSourceType,
    KnowledgeDocument,
)
from app.domain.models.knowledge_version import KnowledgeDocumentRevision
from app.domain.runtime_policy import KnowledgeBaseExecutionPolicy
from app.domain.services.knowledge_base.ingestion_runner import (
    KBIngestionRunner,
    _revision_document,
)
from app.domain.services.knowledge_base.parsers import ParseResult


def test_runner_source_contains_no_destructive_active_index_calls():
    source = inspect.getsource(KBIngestionRunner)

    assert "clear_index_data" not in source
    assert "purge_documents_index_data" not in source
    assert "delete_document" not in source
    assert "replace_candidate_chunks" in source


def test_revision_file_identity_overrides_mutable_logical_projection():
    logical = KnowledgeDocument(
        id="doc-1",
        kb_id="kb-1",
        title="old.txt",
        source_type=KBSourceType.UPLOAD,
        source_ref='{"file_id":"old"}',
        file_id="old",
    )
    revision = KnowledgeDocumentRevision(
        document_id="doc-1",
        source_ref='{"file_id":"new"}',
        source_digest="a" * 64,
    )

    projected = _revision_document(logical, revision)

    assert projected.file_id == "new"
    assert projected.source_ref == '{"file_id":"new"}'
    assert logical.file_id == "old"


@pytest.mark.asyncio
async def test_empty_pdf_parser_keeps_ocr_warning(monkeypatch):
    document = KnowledgeDocument(
        id="doc-1",
        kb_id="kb-1",
        title="empty.pdf",
        source_type=KBSourceType.UPLOAD,
        source_ref='{"file_id":"file-1"}',
        file_id="file-1",
        mime="application/pdf",
    )
    storage = MagicMock()
    storage.download_file = AsyncMock(
        return_value=(
            BytesIO(b"%PDF-1.4"),
            File(
                id="file-1",
                filename="empty.pdf",
                mime_type="application/pdf",
                size=9,
            ),
        )
    )
    runner = KBIngestionRunner(
        uow_factory=MagicMock(),
        file_storage=storage,
        web_documents=MagicMock(),
    )

    async def empty_parse(*_args, **_kwargs):
        return ParseResult(
            blocks=[],
            page_count=0,
            warning="primary empty",
        )

    async def empty_ocr(*_args, **_kwargs):
        return [], "OCR 未执行"

    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner.parse_document",
        empty_parse,
    )
    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner.ocr_pdf_to_blocks",
        empty_ocr,
    )

    with pytest.raises(ValueError, match="OCR 未执行"):
        await runner._parse_document(
            document,
            policy=KnowledgeBaseExecutionPolicy(),
        )
