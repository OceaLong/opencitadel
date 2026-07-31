#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

import pytest

from app.domain.models.knowledge_base import KnowledgeChunk, KnowledgeDocument
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.repositories.knowledge_base_repository import (
    VersionedKnowledgeChunk,
)
from app.domain.services.knowledge_base.retriever import HybridRetriever, RerankSettings


class _FakeKbRepo:
    def __init__(self):
        self.vector_search_chunks = AsyncMock(return_value=[])
        self.bm25_search_chunks = AsyncMock(return_value=[])
        self.get_related_chunk_ids = AsyncMock(return_value=[])
        self.get_chunks_by_ids = AsyncMock(return_value=[])
        self.list_documents = AsyncMock(return_value=[])
        self.get_parents_by_ids = AsyncMock(return_value=[])
        self.vector_search_chunks_for_version = AsyncMock(return_value=[])
        self.bm25_search_chunks_for_version = AsyncMock(return_value=[])
        self.get_related_chunk_ids_for_version = AsyncMock(return_value=[])
        self.get_chunks_by_ids_for_version = AsyncMock(return_value=[])
        self.get_parents_by_ids_for_version = AsyncMock(return_value=[])


class _FakeUow:
    def __init__(self, repo: _FakeKbRepo):
        self.knowledge_base = repo
        self.knowledge_version = MagicMock()
        self.knowledge_version.get_version = AsyncMock(
            return_value=KnowledgeBaseVersion(
                id="kbv1",
                knowledge_base_id="kb-1",
                state=KnowledgeVersionState.READY,
                capabilities={
                    "vector_search": True,
                    "graph_search": False,
                },
                published_at=datetime.now(timezone.utc),
            )
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _chunk(doc_id: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        id="chunk-1",
        kb_id="kb-1",
        doc_id=doc_id,
        version_id="kbv1",
        content="人员能力画像",
        segmented_content="人员 能力 画像",
    )


def _doc(doc_id: str) -> KnowledgeDocument:
    return KnowledgeDocument(
        id=doc_id,
        kb_id="kb-1",
        title="人员手册",
    )


@pytest.mark.anyio
async def test_retriever_falls_back_to_bm25_when_embedding_fails():
    doc = _doc("doc-1")
    chunk = _chunk("doc-1")
    repo = _FakeKbRepo()
    repo.bm25_search_chunks_for_version.return_value = [
        VersionedKnowledgeChunk(
            chunk=chunk,
            document=doc,
            document_revision_id="revision1",
            score=0.9,
        )
    ]

    vector_service = MagicMock()
    vector_service.embed = AsyncMock(side_effect=TimeoutError("embedding timeout"))

    rerank = MagicMock()
    rerank.rerank = AsyncMock(side_effect=lambda query, candidates, top_k: candidates[:top_k])

    retriever = HybridRetriever(
        uow_factory=lambda: _FakeUow(repo),
        vector_service=vector_service,
        rerank_settings=RerankSettings(enabled=False),
    )
    retriever._rerank = rerank

    response = await retriever.retrieve(
        "kb-1",
        "kbv1",
        "人员 能力 画像",
        limit=3,
    )

    vector_service.embed.assert_awaited_once()
    repo.vector_search_chunks_for_version.assert_not_awaited()
    repo.bm25_search_chunks_for_version.assert_awaited_once()
    assert len(response.items) == 1
    assert response.items[0].chunk.id == "chunk-1"
    assert response.degraded_reasons == ("EMBEDDING_UNAVAILABLE",)
