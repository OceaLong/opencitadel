#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Hybrid vector/BM25 retrieval with RRF, rerank, graph expansion, and parent expansion."""
import logging
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from app.domain.external.llm import LLM
from app.domain.models.knowledge_base import KnowledgeChunk, KnowledgeDocument
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.knowledge_base.rerank_service import RerankService, RerankSettings
from app.domain.services.knowledge_base.vector_service import KBVectorService
from app.domain.services.knowledge_base.zh_tokenizer import segment_for_bm25

logger = logging.getLogger(__name__)


@dataclass
class RetrievalSettings:
    vector_top_k: int = 20
    bm25_top_k: int = 20
    rrf_k: int = 60
    final_top_k: int = 8
    graph_enabled: bool = True


@dataclass
class RetrievedChunk:
    chunk: KnowledgeChunk
    document: KnowledgeDocument
    parent: Optional[KnowledgeChunk]
    score: float

    @property
    def content(self) -> str:
        return self.parent.content if self.parent else self.chunk.content


class HybridRetriever:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            llm: Optional[LLM] = None,
            vector_service: Optional[KBVectorService] = None,
            settings: Optional[RetrievalSettings] = None,
            rerank_settings: Optional[RerankSettings] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._llm = llm
        self._vector = vector_service or KBVectorService()
        self._settings = settings or RetrievalSettings()
        self._rerank = RerankService(llm=llm, settings=rerank_settings)

    async def retrieve(self, kb_id: str, query: str, top_k: Optional[int] = None) -> List[RetrievedChunk]:
        started = time.perf_counter()
        final_top_k = top_k or self._settings.final_top_k
        embedding = await self._vector.embed(query)
        segmented_query = segment_for_bm25(query)
        async with self._uow_factory() as uow:
            vector_hits = await uow.knowledge_base.vector_search_chunks(
                kb_id,
                embedding,
                limit=self._settings.vector_top_k,
            )
            bm25_hits = await uow.knowledge_base.bm25_search_chunks(
                kb_id,
                segmented_query,
                limit=self._settings.bm25_top_k,
            )
        candidates = self._rrf_fuse(vector_hits, bm25_hits)
        if not candidates:
            return []
        candidates = await self._expand_graph(kb_id, candidates)
        candidates = await self._attach_parents(candidates)
        candidates = await self._rerank.rerank(query, candidates, final_top_k)
        results = candidates[:final_top_k]
        logger.info(
            "KB 检索完成 kb=%s query_len=%s vector_hits=%s bm25_hits=%s results=%s ms=%s",
            kb_id,
            len(query),
            len(vector_hits),
            len(bm25_hits),
            len(results),
            int((time.perf_counter() - started) * 1000),
        )
        return results

    def _rrf_fuse(self, *ranked_lists) -> List[RetrievedChunk]:
        by_id: dict[str, RetrievedChunk] = {}
        for ranked in ranked_lists:
            for rank, (chunk, doc, score) in enumerate(ranked, start=1):
                fused = 1.0 / (self._settings.rrf_k + rank)
                existing = by_id.get(chunk.id)
                if existing:
                    existing.score += fused
                    continue
                by_id[chunk.id] = RetrievedChunk(chunk=chunk, document=doc, parent=None, score=fused + score * 0.001)
        return sorted(by_id.values(), key=lambda item: item.score, reverse=True)

    async def _expand_graph(self, kb_id: str, candidates: List[RetrievedChunk]) -> List[RetrievedChunk]:
        if not self._settings.graph_enabled:
            return candidates
        chunk_ids = [item.chunk.id for item in candidates]
        async with self._uow_factory() as uow:
            related_ids = await uow.knowledge_base.get_related_chunk_ids(kb_id, chunk_ids, limit=20)
            related_chunks = await uow.knowledge_base.get_chunks_by_ids(related_ids)
            docs = {doc.id: doc for doc in await uow.knowledge_base.list_documents(kb_id)}
        existing = {item.chunk.id for item in candidates}
        out = list(candidates)
        for chunk in related_chunks:
            if chunk.id in existing or chunk.doc_id not in docs:
                continue
            out.append(RetrievedChunk(chunk=chunk, document=docs[chunk.doc_id], parent=None, score=0.0))
            existing.add(chunk.id)
        return out

    async def _attach_parents(self, candidates: List[RetrievedChunk]) -> List[RetrievedChunk]:
        parent_ids = [item.chunk.parent_id for item in candidates if item.chunk.parent_id]
        async with self._uow_factory() as uow:
            parents = {parent.id: parent for parent in await uow.knowledge_base.get_parents_by_ids(parent_ids)}
        for item in candidates:
            if item.chunk.parent_id:
                item.parent = parents.get(item.chunk.parent_id)
        return candidates
