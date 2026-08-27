from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.models.knowledge_base import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEntity,
    KnowledgeRelation,
)
from app.domain.models.knowledge_citation import KnowledgeCitation
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.tool_result import ToolResult
from app.domain.repositories.knowledge_base_repository import (
    VersionedKnowledgeChunk,
)
from app.domain.runtime_policy import (
    KnowledgeRerankPolicy,
    KnowledgeRetrievalPolicy,
    KnowledgeRetrievalRunPolicy,
)
from app.domain.services.knowledge_base.retriever import (
    RetrievalResponse,
    RetrievedChunk,
)
from app.domain.services.tools.knowledge_base_tools import KnowledgeBaseTool

_KB_POLICY = KnowledgeRetrievalRunPolicy(
    vector_enabled=True,
    graph_enabled=True,
    retrieval=KnowledgeRetrievalPolicy(),
    rerank=KnowledgeRerankPolicy(),
)


@pytest.mark.anyio
async def test_kb_search_returns_structured_citation_and_versioned_uri():
    citation = KnowledgeCitation(
        version_id="kbv1",
        document_revision_id="revision-v1",
        doc_id="doc1",
        page_no=2,
        chunk_id="chunk1",
    )
    item = RetrievedChunk(
        chunk=KnowledgeChunk(
            id="chunk1",
            kb_id="kb1",
            doc_id="doc1",
            version_id="kbv1",
            content="policy",
            page_no=2,
        ),
        document=KnowledgeDocument(
            id="doc1",
            kb_id="kb1",
            title="Handbook",
        ),
        parent=None,
        score=0.8,
        citation=citation,
    )
    response = RetrievalResponse(
        items=(item,),
        capabilities={"vector_search": True, "graph_search": False},
        degraded_reasons=(),
    )
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(return_value=response)

    with patch(
        "app.domain.services.tools.knowledge_base_tools.HybridRetriever",
        return_value=retriever,
    ):
        result = await KnowledgeBaseTool(
            uow_factory=MagicMock(),
            kb_id="kb1",
            version_id="kbv1",
            policy=_KB_POLICY,
        ).kb_search("policy", limit=5)

    assert isinstance(result, ToolResult)
    assert result.citations == [citation]
    assert "kbdoc://doc1?page=2&chunk=chunk1&version=kbv1&revision=revision-v1" in result.data
    retriever.retrieve.assert_awaited_once_with("kb1", "kbv1", "policy", limit=5)


@pytest.mark.anyio
async def test_kb_search_renders_presented_parent_page_and_heading():
    citation = KnowledgeCitation(
        version_id="kbv1",
        document_revision_id="revision-v1",
        doc_id="doc1",
        page_no=1,
        chunk_id="parent1",
    )
    item = RetrievedChunk(
        chunk=KnowledgeChunk(
            id="child1",
            kb_id="kb1",
            doc_id="doc1",
            version_id="kbv1",
            parent_id="parent1",
            content="child",
            page_no=2,
            heading_path="Child heading",
        ),
        document=KnowledgeDocument(
            id="doc1",
            kb_id="kb1",
            title="Handbook",
        ),
        parent=KnowledgeChunk(
            id="parent1",
            kb_id="kb1",
            doc_id="doc1",
            version_id="kbv1",
            level="parent",
            content="parent body",
            page_no=1,
            heading_path="Parent heading",
        ),
        score=0.8,
        citation=citation,
    )
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(
        return_value=RetrievalResponse(
            items=(item,),
            capabilities={"vector_search": False, "graph_search": False},
            degraded_reasons=(),
        )
    )

    with patch(
        "app.domain.services.tools.knowledge_base_tools.HybridRetriever",
        return_value=retriever,
    ):
        result = await KnowledgeBaseTool(
            uow_factory=MagicMock(),
            kb_id="kb1",
            version_id="kbv1",
            policy=_KB_POLICY,
        ).kb_search("policy", limit=5)

    assert "《Handbook》p1·Parent heading" in result.data
    assert "p2" not in result.data
    assert "Child heading" not in result.data


@pytest.mark.anyio
async def test_kb_search_caps_requested_limit_with_the_run_policy():
    policy = _KB_POLICY.model_copy(update={"retrieval": KnowledgeRetrievalPolicy(final_top_k=3)})
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(
        return_value=RetrievalResponse(
            items=(),
            capabilities={"vector_search": False, "graph_search": False},
            degraded_reasons=(),
        )
    )

    with patch(
        "app.domain.services.tools.knowledge_base_tools.HybridRetriever",
        return_value=retriever,
    ) as retriever_type:
        await KnowledgeBaseTool(
            uow_factory=MagicMock(),
            kb_id="kb1",
            version_id="kbv1",
            policy=policy,
        ).kb_search("policy", limit=99)

    assert retriever_type.call_args.kwargs["policy"] == policy
    retriever.retrieve.assert_awaited_once_with(
        "kb1",
        "kbv1",
        "policy",
        limit=3,
    )


class _DocumentRepo:
    def __init__(self):
        self.get_document_for_version = AsyncMock(
            return_value=(
                KnowledgeDocument(id="doc1", kb_id="kb1", title="Handbook"),
                "revision-v1",
            )
        )
        from app.domain.repositories import knowledge_base_repository

        page_type = knowledge_base_repository.DocumentPage
        self.read_document_page_for_version = AsyncMock(
            return_value=page_type(
                items=(
                    KnowledgeChunk(
                        id="parent1",
                        kb_id="kb1",
                        doc_id="doc1",
                        version_id="kbv1",
                        level="parent",
                        content="source body",
                        page_no=4,
                        ordinal=1,
                    ),
                ),
                next_cursor="opaque-next",
                total=2,
                truncated=True,
            )
        )


class _Uow:
    def __init__(self, repo, version_repo=None):
        self.knowledge_base = repo
        self.knowledge_version = version_repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.anyio
async def test_get_document_uses_bound_version_and_returns_exact_citations():
    repo = _DocumentRepo()
    result = await KnowledgeBaseTool(
        uow_factory=lambda: _Uow(repo),
        kb_id="kb1",
        version_id="kbv1",
        policy=_KB_POLICY,
    ).get_document("doc1", page=4, cursor="opaque", limit=1)

    assert result.citations == [
        KnowledgeCitation(
            version_id="kbv1",
            document_revision_id="revision-v1",
            doc_id="doc1",
            page_no=4,
            chunk_id="parent1",
        )
    ]
    repo.get_document_for_version.assert_awaited_once_with("kb1", "kbv1", "doc1")
    repo.read_document_page_for_version.assert_awaited_once_with(
        "kb1",
        "kbv1",
        "doc1",
        "revision-v1",
        page_no=4,
        cursor="opaque",
        limit=1,
    )
    assert "opaque-next" in result.data


@pytest.mark.anyio
async def test_get_document_does_not_slice_repository_page_or_citations():
    repo = _DocumentRepo()
    page_type = type(repo.read_document_page_for_version.return_value)
    chunks = tuple(
        KnowledgeChunk(
            id=f"parent-{index:02d}",
            kb_id="kb1",
            doc_id="doc1",
            version_id="kbv1",
            level="parent",
            content=f"source-{index}",
            page_no=1,
            ordinal=index,
        )
        for index in range(25)
    )
    repo.read_document_page_for_version.return_value = page_type(
        items=chunks,
        next_cursor=None,
        total=25,
        truncated=False,
    )

    result = await KnowledgeBaseTool(
        uow_factory=lambda: _Uow(repo),
        kb_id="kb1",
        version_id="kbv1",
        policy=_KB_POLICY,
    ).get_document("doc1", limit=25)

    assert len(result.citations) == 25
    assert [citation.chunk_id for citation in result.citations] == [chunk.id for chunk in chunks]
    assert all(chunk.content in result.data for chunk in chunks)
    assert "next_cursor" not in result.data


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [0, 201])
async def test_get_document_rejects_limit_outside_1_to_200(limit):
    repo = _DocumentRepo()
    tool = KnowledgeBaseTool(
        uow_factory=lambda: _Uow(repo),
        kb_id="kb1",
        version_id="kbv1",
        policy=_KB_POLICY,
    )

    with pytest.raises(ValueError, match="limit"):
        await tool.get_document("doc1", limit=limit)

    repo.get_document_for_version.assert_not_awaited()


class _GraphRepo:
    def __init__(self):
        self.list_entities_for_version = AsyncMock(
            return_value=[
                KnowledgeEntity(
                    id="entity1",
                    kb_id="kb1",
                    version_id="kbv1",
                    name="Citadel",
                )
            ]
        )
        self.list_relations_for_entities_for_version = AsyncMock(return_value=[])
        self.get_chunks_by_ids_for_version = AsyncMock(return_value=[])
        self.get_entities_by_ids_for_version = AsyncMock(return_value=[])


class _VersionRepo:
    def __init__(self, *, graph_search: bool):
        self.get_version = AsyncMock(
            return_value=KnowledgeBaseVersion(
                id="kbv1",
                knowledge_base_id="kb1",
                state=(
                    KnowledgeVersionState.READY if graph_search else KnowledgeVersionState.DEGRADED
                ),
                capabilities={"graph_search": graph_search},
                degraded_reasons=(() if graph_search else ("GRAPH_UNAVAILABLE",)),
                published_at=datetime.now(UTC),
            )
        )


@pytest.mark.anyio
async def test_graph_search_fails_closed_before_partial_graph_reads():
    repo = _GraphRepo()
    version_repo = _VersionRepo(graph_search=False)

    result = await KnowledgeBaseTool(
        uow_factory=lambda: _Uow(repo, version_repo),
        kb_id="kb1",
        version_id="kbv1",
        policy=_KB_POLICY,
    ).graph_search("Citadel")

    assert result.success is False
    assert result.data == "知识图谱检索不可用"
    repo.list_entities_for_version.assert_not_awaited()
    repo.list_relations_for_entities_for_version.assert_not_awaited()
    repo.get_chunks_by_ids_for_version.assert_not_awaited()


@pytest.mark.anyio
async def test_graph_search_reads_exact_version_when_capability_enabled():
    repo = _GraphRepo()
    version_repo = _VersionRepo(graph_search=True)

    result = await KnowledgeBaseTool(
        uow_factory=lambda: _Uow(repo, version_repo),
        kb_id="kb1",
        version_id="kbv1",
        policy=_KB_POLICY,
    ).graph_search("Citadel")

    assert result.success is True
    assert "Citadel" in result.data
    version_repo.get_version.assert_awaited_once_with(
        "kbv1",
        knowledge_base_id="kb1",
    )
    repo.list_entities_for_version.assert_awaited_once()


@pytest.mark.anyio
async def test_graph_search_resolves_missing_endpoint_names_and_evidence():
    repo = _GraphRepo()
    repo.list_relations_for_entities_for_version.return_value = [
        KnowledgeRelation(
            id="relation1",
            kb_id="kb1",
            version_id="kbv1",
            src_entity_id="entity1",
            dst_entity_id="entity2",
            relation="uses",
            chunk_id="chunk1",
        )
    ]
    repo.get_entities_by_ids_for_version.return_value = [
        KnowledgeEntity(
            id="entity2",
            kb_id="kb1",
            version_id="kbv1",
            name="RAG",
            normalized_name="rag",
            type="concept",
        )
    ]
    repo.get_chunks_by_ids_for_version.return_value = [
        VersionedKnowledgeChunk(
            chunk=KnowledgeChunk(
                id="chunk1",
                kb_id="kb1",
                version_id="kbv1",
                doc_id="doc1",
                page_no=7,
            ),
            document=KnowledgeDocument(
                id="doc1",
                kb_id="kb1",
                title="Handbook",
            ),
            document_revision_id="revision-v1",
        )
    ]

    result = await KnowledgeBaseTool(
        uow_factory=lambda: _Uow(repo, _VersionRepo(graph_search=True)),
        kb_id="kb1",
        version_id="kbv1",
        policy=_KB_POLICY,
    ).graph_search("Citadel")

    assert "Citadel --uses--> RAG" in result.data
    assert "entity2" not in result.data
    assert result.citations == [
        KnowledgeCitation(
            version_id="kbv1",
            document_revision_id="revision-v1",
            doc_id="doc1",
            page_no=7,
            chunk_id="chunk1",
        )
    ]
    repo.get_entities_by_ids_for_version.assert_awaited_once_with("kb1", "kbv1", ["entity2"])
