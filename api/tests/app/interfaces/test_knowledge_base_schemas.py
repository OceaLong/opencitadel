from datetime import UTC, datetime

from app.interfaces.schemas.knowledge_base import (
    KnowledgeBaseResponse,
    ListKnowledgeDocumentsResponse,
)


def test_kb_response_has_ready_doc_count_default_zero():
    resp = KnowledgeBaseResponse(
        id="kb1",
        name="t",
        status="ready",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert resp.ready_doc_count == 0


def test_list_documents_response_carries_total():
    resp = ListKnowledgeDocumentsResponse(documents=[], total=7)
    assert resp.total == 7
