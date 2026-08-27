"""Version-closed hybrid retrieval with truthful runtime degradation."""

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.domain.external.llm import LLM
from app.domain.models.knowledge_base import KnowledgeChunk, KnowledgeDocument
from app.domain.models.knowledge_citation import KnowledgeCitation
from app.domain.models.knowledge_version import (
    FrozenMapping,
    KnowledgeVersionState,
)
from app.domain.repositories.knowledge_base_repository import (
    KNOWLEDGE_EMBEDDING_DIMENSION,
    VersionedKnowledgeChunk,
)
from app.domain.repositories.uow import IUnitOfWork
from app.domain.runtime_policy import KnowledgeRetrievalRunPolicy
from app.domain.services.knowledge_base.rerank_service import RerankService
from app.domain.services.knowledge_base.vector_service import KBVectorService
from app.domain.services.knowledge_base.zh_tokenizer import segment_for_bm25

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: KnowledgeChunk
    document: KnowledgeDocument
    parent: KnowledgeChunk | None
    score: float
    citation: KnowledgeCitation

    @property
    def content(self) -> str:
        return self.parent.content if self.parent else self.chunk.content


@dataclass(frozen=True)
class RetrievalResponse:
    items: tuple[RetrievedChunk, ...]
    capabilities: FrozenMapping
    degraded_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(
            self,
            "capabilities",
            FrozenMapping(self.capabilities or {}),
        )
        object.__setattr__(
            self,
            "degraded_reasons",
            tuple(self.degraded_reasons or ()),
        )


class HybridRetriever:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        *,
        policy: KnowledgeRetrievalRunPolicy,
        llm: LLM | None = None,
        vector_service: KBVectorService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._llm = llm
        self._vector = vector_service
        self._policy = policy
        self._rerank = RerankService(llm=llm, policy=policy.rerank)

    async def retrieve(
        self,
        kb_id: str,
        version_id: str,
        query: str,
        limit: int,
    ) -> RetrievalResponse:
        if not str(kb_id or "").strip():
            raise ValueError("knowledge base id is required")
        if not str(version_id or "").strip():
            raise ValueError("knowledge-base version id is required")
        if limit < 1:
            raise ValueError("retrieval limit must be positive")
        started = time.perf_counter()
        final_top_k = min(limit, self._policy.retrieval.final_top_k)
        async with self._uow_factory() as uow:
            version = await uow.knowledge_version.get_version(
                version_id,
                knowledge_base_id=kb_id,
            )
        if (
            version is None
            or version.id != version_id
            or version.knowledge_base_id != kb_id
            or version.published_at is None
            or version.state
            not in {
                KnowledgeVersionState.READY,
                KnowledgeVersionState.DEGRADED,
            }
        ):
            raise ValueError("knowledge-base version is not published")

        capabilities = dict(version.capabilities)
        capabilities.setdefault("keyword_search", True)
        capabilities.setdefault("vector_search", False)
        capabilities.setdefault("graph_search", False)
        reasons = list(version.degraded_reasons)
        embedding: list[float] = []
        vector_enabled = bool(
            self._policy.vector_enabled
            and capabilities["vector_search"]
            and self._vector is not None
        )
        if not self._policy.vector_enabled:
            capabilities["vector_search"] = False
        if vector_enabled:
            try:
                assert self._vector is not None
                raw_embedding = await self._vector.embed(query)
                if not self._valid_embedding(raw_embedding):
                    raise ValueError("embedding is empty or malformed")
                embedding = [float(item) for item in raw_embedding]
            except (OSError, RuntimeError, ValueError):
                logger.warning(
                    "KB embedding 失败，降级为 BM25-only kb=%s version=%s",
                    kb_id,
                    version_id,
                    exc_info=True,
                )
                capabilities["vector_search"] = False
                reasons.append("EMBEDDING_UNAVAILABLE")

        segmented_query = segment_for_bm25(query)
        async with self._uow_factory() as uow:
            bm25_hits = await uow.knowledge_base.bm25_search_chunks_for_version(
                kb_id,
                version_id,
                segmented_query,
                limit=self._policy.retrieval.bm25_top_k,
            )
        vector_hits: list[VersionedKnowledgeChunk] = []
        if capabilities["vector_search"]:
            try:
                # Keep optional ANN failure in its own transaction so an
                # unsupported iterative-scan setting or SQL error cannot
                # poison the already-successful mandatory BM25 read.
                async with self._uow_factory() as uow:
                    vector_hits = await uow.knowledge_base.vector_search_chunks_for_version(
                        kb_id,
                        version_id,
                        embedding,
                        limit=self._policy.retrieval.vector_top_k,
                    )
            except (OSError, RuntimeError, ValueError):
                logger.warning(
                    "KB vector SQL 失败，降级为 BM25-only kb=%s version=%s",
                    kb_id,
                    version_id,
                    exc_info=True,
                )
                capabilities["vector_search"] = False
                reasons.append("EMBEDDING_UNAVAILABLE")

        candidates = self._rrf_fuse(
            kb_id,
            version_id,
            vector_hits,
            bm25_hits,
        )
        if not candidates:
            if not self._policy.graph_enabled:
                capabilities["graph_search"] = False
            return RetrievalResponse(
                items=(),
                capabilities=capabilities,
                degraded_reasons=self._dedupe_reasons(reasons),
            )

        if capabilities["graph_search"] and self._policy.graph_enabled:
            try:
                candidates = await self._expand_graph(
                    kb_id,
                    version_id,
                    candidates,
                )
            except (OSError, RuntimeError, ValueError):
                logger.warning(
                    "KB graph expansion failed kb=%s version=%s",
                    kb_id,
                    version_id,
                    exc_info=True,
                )
                capabilities["graph_search"] = False
                reasons.append("GRAPH_UNAVAILABLE")
        else:
            capabilities["graph_search"] = False
        candidates = await self._attach_parents(
            kb_id,
            version_id,
            candidates,
        )
        candidates = await self._rerank.rerank(
            query,
            candidates,
            final_top_k,
        )
        results = tuple(candidates[:final_top_k])
        logger.info(
            "KB 检索完成 kb=%s version=%s query_len=%s vector_hits=%s "
            "bm25_hits=%s results=%s ms=%s",
            kb_id,
            version_id,
            len(query),
            len(vector_hits),
            len(bm25_hits),
            len(results),
            int((time.perf_counter() - started) * 1000),
        )
        return RetrievalResponse(
            items=results,
            capabilities=capabilities,
            degraded_reasons=self._dedupe_reasons(reasons),
        )

    def _rrf_fuse(
        self,
        kb_id: str,
        version_id: str,
        *ranked_lists: list[VersionedKnowledgeChunk],
    ) -> list[RetrievedChunk]:
        by_id: dict[str, RetrievedChunk] = {}
        for ranked in ranked_lists:
            for rank, record in enumerate(ranked, start=1):
                if (
                    record.chunk.kb_id != kb_id
                    or record.document.kb_id != kb_id
                    or record.chunk.version_id != version_id
                    or record.chunk.doc_id != record.document.id
                ):
                    continue
                if not str(record.document_revision_id or "").strip():
                    raise ValueError("retrieval record is missing document revision identity")
                fused = 1.0 / (self._policy.retrieval.rrf_k + rank)
                existing = by_id.get(record.chunk.id)
                if existing:
                    by_id[record.chunk.id] = RetrievedChunk(
                        chunk=existing.chunk,
                        document=existing.document,
                        parent=existing.parent,
                        score=existing.score + fused,
                        citation=existing.citation,
                    )
                    continue
                by_id[record.chunk.id] = RetrievedChunk(
                    chunk=record.chunk,
                    document=record.document,
                    parent=None,
                    score=fused + record.score * 0.001,
                    citation=KnowledgeCitation(
                        version_id=version_id,
                        document_revision_id=record.document_revision_id,
                        doc_id=record.document.id,
                        page_no=record.chunk.page_no,
                        chunk_id=record.chunk.id,
                    ),
                )
        return sorted(
            by_id.values(),
            key=lambda item: (-item.score, item.chunk.id),
        )

    async def _expand_graph(
        self,
        kb_id: str,
        version_id: str,
        candidates: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        chunk_ids = [item.chunk.id for item in candidates]
        async with self._uow_factory() as uow:
            related_ids = await uow.knowledge_base.get_related_chunk_ids_for_version(
                kb_id,
                version_id,
                chunk_ids,
                limit=20,
            )
            related_chunks = await uow.knowledge_base.get_chunks_by_ids_for_version(
                kb_id,
                version_id,
                related_ids,
            )
        existing = {item.chunk.id for item in candidates}
        out = list(candidates)
        for record in related_chunks:
            if (
                record.chunk.id in existing
                or record.chunk.kb_id != kb_id
                or record.chunk.version_id != version_id
                or record.document.kb_id != kb_id
                or record.chunk.doc_id != record.document.id
            ):
                continue
            if not str(record.document_revision_id or "").strip():
                raise ValueError("graph record is missing document revision identity")
            out.append(
                RetrievedChunk(
                    chunk=record.chunk,
                    document=record.document,
                    parent=None,
                    score=0.0,
                    citation=KnowledgeCitation(
                        version_id=version_id,
                        document_revision_id=record.document_revision_id,
                        doc_id=record.document.id,
                        page_no=record.chunk.page_no,
                        chunk_id=record.chunk.id,
                    ),
                )
            )
            existing.add(record.chunk.id)
        return out

    async def _attach_parents(
        self,
        kb_id: str,
        version_id: str,
        candidates: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        parent_ids = sorted({item.chunk.parent_id for item in candidates if item.chunk.parent_id})
        async with self._uow_factory() as uow:
            parents = {
                parent.id: parent
                for parent in (
                    await uow.knowledge_base.get_parents_by_ids_for_version(
                        kb_id,
                        version_id,
                        parent_ids,
                    )
                )
                if (parent.kb_id == kb_id and parent.version_id == version_id)
            }
        out: list[RetrievedChunk] = []
        for item in candidates:
            parent = parents.get(item.chunk.parent_id) if item.chunk.parent_id else None
            if parent is not None and parent.doc_id != item.chunk.doc_id:
                parent = None
            citation = item.citation
            if parent is not None:
                citation = KnowledgeCitation(
                    version_id=citation.version_id,
                    document_revision_id=citation.document_revision_id,
                    doc_id=citation.doc_id,
                    page_no=(parent.page_no if parent.page_no is not None else citation.page_no),
                    chunk_id=parent.id,
                )
            out.append(
                RetrievedChunk(
                    chunk=item.chunk,
                    document=item.document,
                    parent=parent,
                    score=item.score,
                    citation=citation,
                )
            )
        return out

    @staticmethod
    def _valid_embedding(value: object) -> bool:
        if not isinstance(value, (list, tuple)) or len(value) != KNOWLEDGE_EMBEDDING_DIMENSION:
            return False
        try:
            return all(math.isfinite(float(item)) for item in value)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _dedupe_reasons(reasons: list[str]) -> tuple[str, ...]:
        out: list[str] = []
        seen: set[str] = set()
        for reason in reasons:
            normalized = str(reason or "").strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                out.append(normalized)
        return tuple(out)
