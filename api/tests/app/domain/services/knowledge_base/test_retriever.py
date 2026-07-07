#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.knowledge_base import KnowledgeChunk, KnowledgeDocument
from app.domain.services.knowledge_base.retriever import HybridRetriever, RerankSettings


class _FakeKbRepo:
    def __init__(self):
        self.vector_search_chunks = AsyncMock(return_value=[])
        self.bm25_search_chunks = AsyncMock(return_value=[])
        self.get_related_chunk_ids = AsyncMock(return_value=[])
        self.get_chunks_by_ids = AsyncMock(return_value=[])
        self.list_documents = AsyncMock(return_value=[])
        self.get_parents_by_ids = AsyncMock(return_value=[])


class _FakeUow:
    def __init__(self, repo: _FakeKbRepo):
        self.knowledge_base = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _chunk(doc_id: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        id="chunk-1",
        kb_id="kb-1",
        doc_id=doc_id,
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
    repo.bm25_search_chunks.return_value = [(chunk, doc, 0.9)]

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

    results = await retriever.retrieve("kb-1", "人员 能力 画像", top_k=3)

    vector_service.embed.assert_awaited_once()
    repo.vector_search_chunks.assert_awaited_once()
    repo.bm25_search_chunks.assert_awaited_once()
    assert len(results) == 1
    assert results[0].chunk.id == "chunk-1"
