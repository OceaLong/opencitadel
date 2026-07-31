#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.knowledge_base import (
    KnowledgeChunk,
    KnowledgeDocument,
)
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.repositories.knowledge_base_repository import (
    KNOWLEDGE_EMBEDDING_DIMENSION,
    VersionedKnowledgeChunk,
)
from app.domain.services.knowledge_base.retriever import (
    HybridRetriever,
    RerankSettings,
)


class _VersionRepo:
    def __init__(self, version: KnowledgeBaseVersion | None):
        self.get_version = AsyncMock(return_value=version)


class _KnowledgeRepo:
    def __init__(self):
        self.vector_search_chunks_for_version = AsyncMock(return_value=[])
        self.bm25_search_chunks_for_version = AsyncMock(return_value=[])
        self.get_related_chunk_ids_for_version = AsyncMock(return_value=[])
        self.get_chunks_by_ids_for_version = AsyncMock(return_value=[])
        self.get_parents_by_ids_for_version = AsyncMock(return_value=[])


class _Uow:
    def __init__(self, knowledge, version):
        self.knowledge_base = knowledge
        self.knowledge_version = version

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _version(
    version_id: str = "kbv1",
    *,
    capabilities: dict[str, bool] | None = None,
    state: KnowledgeVersionState = KnowledgeVersionState.READY,
    published: bool = True,
) -> KnowledgeBaseVersion:
    return KnowledgeBaseVersion(
        id=version_id,
        knowledge_base_id="kb1",
        state=state,
        capabilities=capabilities
        or {"vector_search": True, "graph_search": False},
        degraded_reasons=(
            ("VECTOR_INDEX_UNAVAILABLE",)
            if state is KnowledgeVersionState.DEGRADED
            else ()
        ),
        published_at=datetime.now(timezone.utc) if published else None,
    )


def _record(
    *,
    version_id: str = "kbv1",
    chunk_id: str = "chunk-v1",
    revision_id: str | None = "revision-v1",
    content: str = "v1 only",
    parent_id: str | None = None,
    score: float = 0.9,
) -> VersionedKnowledgeChunk:
    chunk = KnowledgeChunk(
        id=chunk_id,
        kb_id="kb1",
        doc_id="doc1",
        version_id=version_id,
        parent_id=parent_id,
        content=content,
        page_no=2,
    )
    doc = KnowledgeDocument(id="doc1", kb_id="kb1", title="handbook")
    return VersionedKnowledgeChunk(
        chunk=chunk,
        document=doc,
        document_revision_id=revision_id,
        score=score,
    )


def _retriever(
    repo: _KnowledgeRepo,
    version: KnowledgeBaseVersion | None,
    vector=None,
) -> HybridRetriever:
    uow = _Uow(repo, _VersionRepo(version))
    retriever = HybridRetriever(
        uow_factory=lambda: uow,
        vector_service=vector,
        rerank_settings=RerankSettings(enabled=False),
    )
    retriever._rerank = MagicMock()
    retriever._rerank.rerank = AsyncMock(
        side_effect=lambda query, candidates, top_k: candidates[:top_k]
    )
    return retriever


def _valid_embedding(value: float = 0.1) -> list[float]:
    return [value] * KNOWLEDGE_EMBEDDING_DIMENSION


@pytest.mark.anyio
async def test_retrieval_never_crosses_bound_version_and_cites_manifest_revision():
    repo = _KnowledgeRepo()
    repo.bm25_search_chunks_for_version.return_value = [
        _record(),
        _record(
            version_id="kbv2",
            chunk_id="chunk-v2",
            revision_id="revision-v2",
            content="v2 only",
        ),
    ]
    vector = MagicMock()
    vector.embed = AsyncMock(return_value=_valid_embedding())
    response = await _retriever(repo, _version(), vector).retrieve(
        "kb1", "kbv1", "release policy", limit=10
    )

    assert [item.content for item in response.items] == ["v1 only"]
    assert response.items[0].citation.model_dump(mode="json") == {
        "version_id": "kbv1",
        "document_revision_id": "revision-v1",
        "doc_id": "doc1",
        "page_no": 2,
        "chunk_id": "chunk-v1",
    }
    repo.bm25_search_chunks_for_version.assert_awaited_once_with(
        "kb1", "kbv1", "release policy", limit=20
    )


@pytest.mark.anyio
async def test_embedding_failure_returns_bm25_with_truthful_effective_metadata():
    repo = _KnowledgeRepo()
    repo.bm25_search_chunks_for_version.return_value = [_record()]
    vector = MagicMock()
    vector.embed = AsyncMock(side_effect=TimeoutError("embedding timeout"))

    response = await _retriever(repo, _version(), vector).retrieve(
        "kb1", "kbv1", "关键词", limit=5
    )

    assert response.items
    assert response.capabilities["vector_search"] is False
    assert response.degraded_reasons == ("EMBEDDING_UNAVAILABLE",)
    repo.vector_search_chunks_for_version.assert_not_awaited()


@pytest.mark.anyio
async def test_wrong_embedding_dimension_never_reaches_vector_sql():
    repo = _KnowledgeRepo()
    repo.bm25_search_chunks_for_version.return_value = [_record()]
    vector = MagicMock()
    vector.embed = AsyncMock(return_value=[0.1] * 1535)

    response = await _retriever(repo, _version(), vector).retrieve(
        "kb1", "kbv1", "keyword", limit=3
    )

    assert [item.chunk.id for item in response.items] == ["chunk-v1"]
    assert response.capabilities["vector_search"] is False
    assert response.degraded_reasons == ("EMBEDDING_UNAVAILABLE",)
    repo.vector_search_chunks_for_version.assert_not_awaited()


@pytest.mark.anyio
async def test_vector_disabled_skips_embedding_and_vector_sql():
    repo = _KnowledgeRepo()
    repo.bm25_search_chunks_for_version.return_value = [_record()]
    vector = MagicMock()
    vector.embed = AsyncMock()

    response = await _retriever(
        repo,
        _version(capabilities={
            "vector_search": False,
            "graph_search": False,
        }),
        vector,
    ).retrieve("kb1", "kbv1", "keyword", limit=3)

    assert response.items
    assert response.capabilities["vector_search"] is False
    vector.embed.assert_not_awaited()
    repo.vector_search_chunks_for_version.assert_not_awaited()


@pytest.mark.anyio
async def test_bm25_failure_is_not_silently_converted_to_empty_success():
    repo = _KnowledgeRepo()
    repo.bm25_search_chunks_for_version.side_effect = RuntimeError("tsvector down")
    vector = MagicMock()
    vector.embed = AsyncMock(return_value=_valid_embedding())

    with pytest.raises(RuntimeError, match="tsvector down"):
        await _retriever(repo, _version(), vector).retrieve(
            "kb1", "kbv1", "keyword", limit=3
        )


@pytest.mark.anyio
async def test_missing_revision_identity_fails_closed():
    repo = _KnowledgeRepo()
    repo.bm25_search_chunks_for_version.return_value = [
        _record(revision_id=None)
    ]
    vector = MagicMock()
    vector.embed = AsyncMock(return_value=_valid_embedding())

    with pytest.raises(ValueError, match="revision"):
        await _retriever(repo, _version(), vector).retrieve(
            "kb1", "kbv1", "keyword", limit=3
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("version", "message"),
    [
        (_version(state=KnowledgeVersionState.BUILDING, published=False), "published"),
        (_version(state=KnowledgeVersionState.FAILED, published=False), "published"),
        (None, "published"),
    ],
)
async def test_candidate_failed_and_missing_versions_return_no_partial_data(
    version,
    message,
):
    repo = _KnowledgeRepo()
    vector = MagicMock()
    vector.embed = AsyncMock(return_value=_valid_embedding())

    with pytest.raises(ValueError, match=message):
        await _retriever(repo, version, vector).retrieve(
            "kb1", "kbv1", "keyword", limit=3
        )

    repo.bm25_search_chunks_for_version.assert_not_awaited()
    repo.vector_search_chunks_for_version.assert_not_awaited()


@pytest.mark.anyio
async def test_parent_expansion_rejects_cross_version_parent():
    repo = _KnowledgeRepo()
    repo.bm25_search_chunks_for_version.return_value = [
        _record(parent_id="parent-v1")
    ]
    repo.get_parents_by_ids_for_version.return_value = [
        KnowledgeChunk(
            id="parent-v1",
            kb_id="kb1",
            doc_id="doc1",
            version_id="kbv2",
            content="cross version parent",
        )
    ]
    vector = MagicMock()
    vector.embed = AsyncMock(return_value=_valid_embedding())

    response = await _retriever(repo, _version(), vector).retrieve(
        "kb1", "kbv1", "keyword", limit=3
    )

    assert response.items[0].content == "v1 only"
    repo.get_parents_by_ids_for_version.assert_awaited_once_with(
        "kb1", "kbv1", ["parent-v1"]
    )


@pytest.mark.anyio
async def test_vector_sql_failure_keeps_bm25_and_one_degradation_reason():
    repo = _KnowledgeRepo()
    repo.bm25_search_chunks_for_version.return_value = [_record()]
    repo.vector_search_chunks_for_version.side_effect = RuntimeError(
        "pgvector unavailable"
    )
    vector = MagicMock()
    vector.embed = AsyncMock(return_value=_valid_embedding())

    response = await _retriever(repo, _version(), vector).retrieve(
        "kb1", "kbv1", "keyword", limit=3
    )

    assert [item.chunk.id for item in response.items] == ["chunk-v1"]
    assert response.capabilities["vector_search"] is False
    assert response.degraded_reasons == ("EMBEDDING_UNAVAILABLE",)


@pytest.mark.anyio
async def test_vector_transaction_rolls_back_before_bm25_fallback_returns():
    repo = _KnowledgeRepo()
    repo.bm25_search_chunks_for_version.return_value = [_record()]
    repo.vector_search_chunks_for_version.side_effect = RuntimeError(
        "hnsw.iterative_scan unsupported"
    )
    version_repo = _VersionRepo(_version())
    exits: list[tuple[int, object]] = []
    created = 0

    class _TransactionalUow(_Uow):
        def __init__(self, index):
            super().__init__(repo, version_repo)
            self.index = index

        async def __aexit__(self, exc_type, exc, tb):
            exits.append((self.index, exc_type))
            # The third UoW is the optional vector transaction. If its
            # database error was swallowed inside the context, a real
            # PostgreSQL commit would fail because the transaction is
            # aborted. The fallback contract requires exception-driven
            # rollback before the outer catch.
            if self.index == 2 and exc_type is None:
                raise RuntimeError("commit on aborted vector transaction")
            return False

    def factory():
        nonlocal created
        uow = _TransactionalUow(created)
        created += 1
        return uow

    vector = MagicMock()
    vector.embed = AsyncMock(return_value=_valid_embedding())
    retriever = HybridRetriever(
        uow_factory=factory,
        vector_service=vector,
        rerank_settings=RerankSettings(enabled=False),
    )
    retriever._rerank = MagicMock()
    retriever._rerank.rerank = AsyncMock(
        side_effect=lambda query, candidates, top_k: candidates[:top_k]
    )

    response = await retriever.retrieve(
        "kb1", "kbv1", "keyword", limit=3
    )

    assert [item.chunk.id for item in response.items] == ["chunk-v1"]
    assert response.capabilities["vector_search"] is False
    assert exits[2][1] is RuntimeError


@pytest.mark.anyio
async def test_graph_failure_keeps_core_results_and_degrades_graph_only():
    repo = _KnowledgeRepo()
    repo.bm25_search_chunks_for_version.return_value = [_record()]
    repo.get_related_chunk_ids_for_version.side_effect = RuntimeError(
        "graph unavailable"
    )
    vector = MagicMock()
    vector.embed = AsyncMock()

    response = await _retriever(
        repo,
        _version(capabilities={
            "vector_search": False,
            "graph_search": True,
        }),
        vector,
    ).retrieve("kb1", "kbv1", "keyword", limit=3)

    assert [item.chunk.id for item in response.items] == ["chunk-v1"]
    assert response.capabilities["graph_search"] is False
    assert response.degraded_reasons == ("GRAPH_UNAVAILABLE",)


@pytest.mark.anyio
async def test_rrf_ties_and_limit_are_deterministic():
    repo = _KnowledgeRepo()
    repo.bm25_search_chunks_for_version.return_value = [
        _record(chunk_id="chunk-b", content="b", score=0.0),
        _record(chunk_id="chunk-a", content="a", score=0.0),
    ]
    vector = MagicMock()
    vector.embed = AsyncMock()

    response = await _retriever(
        repo,
        _version(capabilities={
            "vector_search": False,
            "graph_search": False,
        }),
        vector,
    ).retrieve("kb1", "kbv1", "keyword", limit=1)

    assert len(response.items) == 1
    assert response.items[0].chunk.id == "chunk-b"


@pytest.mark.anyio
async def test_degradation_reasons_are_stably_deduplicated():
    repo = _KnowledgeRepo()
    repo.bm25_search_chunks_for_version.return_value = [_record()]
    vector = MagicMock()
    vector.embed = AsyncMock(side_effect=TimeoutError())
    version = _version(
        state=KnowledgeVersionState.DEGRADED,
        capabilities={"vector_search": True, "graph_search": False},
    ).model_copy(
        update={
            "degraded_reasons": (
                "VECTOR_INDEX_UNAVAILABLE",
                "EMBEDDING_UNAVAILABLE",
                "VECTOR_INDEX_UNAVAILABLE",
            )
        }
    )

    response = await _retriever(repo, version, vector).retrieve(
        "kb1", "kbv1", "keyword", limit=3
    )

    assert response.degraded_reasons == (
        "VECTOR_INDEX_UNAVAILABLE",
        "EMBEDDING_UNAVAILABLE",
    )
