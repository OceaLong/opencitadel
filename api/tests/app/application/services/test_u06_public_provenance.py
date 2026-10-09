"""Public artifact associations retain exact cut and disclose no global sequence."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.services.execution_content_service import ExecutionContentService
from app.domain.models.artifact_provenance import ArtifactVersionProvenance
from app.domain.models.scope import OwnerScope
from app.interfaces.schemas.execution_view import ArtifactProvenanceResponse


@pytest.mark.asyncio
async def test_cut_filters_future_and_other_run_and_keeps_plural_steps_and_safe_citations():
    run, other = uuid4(), uuid4()

    def row(producer_run, boundary):
        return ArtifactVersionProvenance(
            id=uuid4(),
            artifact_id="a",
            version=1,
            producer_identity=str(uuid4()),
            evidence_kind="direct",
            binding_status="bound",
            content_digest=None,
            producer_run_id=producer_run,
            produced_event_id=uuid4(),
            boundary=boundary,
            producer_step_ids=("s1", "s2"),
            citation_refs=[
                {
                    "citation_id": "canonical",
                    "version_id": "v1",
                    "document_revision_id": "r1",
                    "doc_id": "doc",
                    "availability": "available",
                }
            ],
            evidence={"secret": "NEVER"},
        )

    visible, future, foreign = row(run, 900), row(run, 1100), row(other, 2)

    @asynccontextmanager
    async def uow():
        yield SimpleNamespace(
            artifact=SimpleNamespace(
                get_by_id=AsyncMock(
                    return_value=SimpleNamespace(version_refs=["x"], session_id="session")
                )
            ),
            session=SimpleNamespace(get_metadata=AsyncMock(return_value={})),
            artifact_provenance=SimpleNamespace(
                get_version=AsyncMock(return_value=[visible, future, foreign])
            ),
        )

    views = SimpleNamespace(
        get_production_cut=AsyncMock(
            return_value=(
                SimpleNamespace(formal_position=1000),
                {
                    str(visible.produced_event_id): {
                        "production_order": 4,
                        "production_observed_at": "2026-09-09T00:00:00Z",
                    }
                },
            )
        )
    )
    service = ExecutionContentService(
        uow, views, None, cursor_secret=b"u06-public-provenance-secret"
    )
    result = await service.get_public_provenance(
        OwnerScope.personal("u"), "a", 1, run_id=run, at="opaque"
    )
    assert len(result) == 1
    public = ArtifactProvenanceResponse.model_validate(result[0]).model_dump(mode="json")
    assert public["producer_step_ids"] == ["s1", "s2"]
    assert public["citation_refs"][0]["citation_id"] == "canonical"
    assert public["production_order"] == 4
    assert "boundary" not in public
    assert "evidence" not in public
    assert "content_digest" not in public


@pytest.mark.asyncio
async def test_run_and_cut_are_all_or_nothing():
    from app.application.ports.execution_view import ViewCursorInvalid

    service = ExecutionContentService(
        None, None, None, cursor_secret=b"u06-public-provenance-secret"
    )
    with pytest.raises(ViewCursorInvalid):
        await service.get_public_provenance(OwnerScope.personal("u"), "a", 1, run_id=uuid4())


@pytest.mark.asyncio
async def test_only_missing_chunk_after_fixed_source_authority_is_recoverable():
    from app.application.ports.execution_view import ViewNotFound
    from app.domain.models.resource_pin import ResourceUnavailable

    canonical = {
        "citation_id": "c",
        "resource_kind": "knowledge_base",
        "knowledge_base_id": "kb",
        "version_id": "v1",
        "doc_id": "d",
        "document_revision_id": "r1",
        "chunk_id": "gone",
        "page_no": 2,
        "availability": "available",
    }
    kb = SimpleNamespace(
        get_kb=AsyncMock(return_value=object()),
        get_document_for_version=AsyncMock(
            return_value=(SimpleNamespace(title="Fixed title"), "r1")
        ),
        get_chunks_by_ids_for_version=AsyncMock(return_value=[]),
        read_document_page_for_version=AsyncMock(
            return_value=SimpleNamespace(
                items=[SimpleNamespace(content="Same revision page")], next_cursor=None
            )
        ),
    )

    @asynccontextmanager
    async def uow():
        yield SimpleNamespace(
            knowledge_base=kb,
            execution_content=SimpleNamespace(get_citation=AsyncMock(return_value=canonical)),
        )

    service = ExecutionContentService(
        uow, None, None, cursor_secret=b"u06-public-provenance-secret"
    )
    missing = await service.read_source(OwnerScope.personal("u"), {"citation_id": "c"})
    assert missing.availability == "unavailable"
    assert missing.reason == "source_locator_unavailable"
    assert missing.content is None
    assert missing.next_cursor is None
    page = await service.read_source(
        OwnerScope.personal("u"), {"citation_id": "c", "chunk_id": None}
    )
    assert page.content == "Same revision page"
    assert page.source_title == "Fixed title"
    duplicate = SimpleNamespace(
        chunk=SimpleNamespace(id="gone", kb_id="kb", version_id="v1", doc_id="d", page_no=2),
        document_revision_id="r1",
    )
    kb.get_chunks_by_ids_for_version.return_value = [duplicate, duplicate]
    with pytest.raises(ResourceUnavailable, match="ambiguous"):
        await service.read_source(OwnerScope.personal("u"), {"citation_id": "c"})
    kb.get_chunks_by_ids_for_version.return_value = []
    kb.get_kb.side_effect = [object(), object(), None]
    with pytest.raises(ViewNotFound):
        await service.read_source(OwnerScope.personal("u"), {"citation_id": "c"})
    kb.get_kb.side_effect = None
    kb.get_kb.return_value = None
    with pytest.raises(ViewNotFound):
        await service.read_source(OwnerScope.personal("u"), {"citation_id": "c", "chunk_id": None})
    kb.get_kb.return_value = object()
    kb.get_document_for_version.return_value = (SimpleNamespace(title="Different revision"), "r2")
    with pytest.raises((ViewNotFound, ResourceUnavailable)):
        await service.read_source(OwnerScope.personal("u"), {"citation_id": "c"})
