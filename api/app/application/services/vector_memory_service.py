#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Vector memory with pgvector + OpenAI-compatible embeddings."""
import logging
from collections import OrderedDict
from typing import List, Optional

from openai import AsyncOpenAI
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.config_provider import get_runtime_config
from app.domain.models.memory_entry import MemoryEntry, MemoryScope, MemorySource
from app.infrastructure.models.memory_entry import MemoryEntryORM
from core.config import get_settings

logger = logging.getLogger(__name__)
_EMBEDDING_DIM = 1536
_EMBEDDING_CACHE_MAX_SIZE = 256
_EMBEDDING_BATCH_SIZE = 64
_vector_memory_service: Optional["VectorMemoryService"] = None


class VectorMemoryService:
    """pgvector-backed semantic memory recall."""

    def __init__(self) -> None:
        memory = get_runtime_config().memory
        settings = get_settings()
        self.enabled = memory.vector_enabled
        self.embedding_provider = memory.embedding.provider
        self.embedding_model = memory.embedding.model
        self._openai_api_key = settings.embedding_api_key or ""
        self._openai_base_url = memory.embedding.base_url or "https://api.openai.com/v1"
        self._embedding_timeout = memory.embedding.timeout_seconds
        self._embedding_cache: OrderedDict[str, List[float]] = OrderedDict()
        self._client = AsyncOpenAI(
            api_key=self._openai_api_key or "sk-placeholder",
            base_url=self._openai_base_url,
            timeout=self._embedding_timeout,
        )

    def _cache_get(self, content: str) -> Optional[List[float]]:
        cached = self._embedding_cache.get(content)
        if cached is None:
            return None
        self._embedding_cache.move_to_end(content)
        return cached

    def _cache_set(self, content: str, vector: List[float]) -> None:
        self._embedding_cache[content] = vector
        self._embedding_cache.move_to_end(content)
        while len(self._embedding_cache) > _EMBEDDING_CACHE_MAX_SIZE:
            self._embedding_cache.popitem(last=False)

    async def embed(self, content: str) -> List[float]:
        if not self.enabled or not content.strip():
            return []
        vectors = await self.embed_batch([content])
        return vectors[0] if vectors else []

    async def embed_batch(self, contents: List[str]) -> List[List[float]]:
        if not self.enabled:
            return [[] for _ in contents]
        return await self.embed_batch_unconditional(contents)

    async def embed_batch_unconditional(self, contents: List[str]) -> List[List[float]]:
        """Embed texts regardless of memory.vector_enabled (for KB indexing)."""
        results: List[Optional[List[float]]] = [None] * len(contents)
        uncached_indices: List[int] = []
        uncached_texts: List[str] = []

        for index, content in enumerate(contents):
            if not content.strip():
                results[index] = []
                continue
            cached = self._cache_get(content)
            if cached is not None:
                results[index] = cached
            else:
                uncached_indices.append(index)
                uncached_texts.append(content)

        for batch_start in range(0, len(uncached_texts), _EMBEDDING_BATCH_SIZE):
            batch_indices = uncached_indices[batch_start:batch_start + _EMBEDDING_BATCH_SIZE]
            batch_texts = uncached_texts[batch_start:batch_start + _EMBEDDING_BATCH_SIZE]
            if not batch_texts:
                continue
            response = await self._client.embeddings.create(
                model=self.embedding_model,
                input=batch_texts,
            )
            for offset, item in enumerate(response.data):
                vector = item.embedding
                content = batch_texts[offset]
                self._cache_set(content, vector)
                results[batch_indices[offset]] = vector

        return [vector if vector is not None else [] for vector in results]

    async def store_embedding(
            self,
            entry_id: str,
            content: str,
            db_session: Optional[AsyncSession] = None,
    ) -> None:
        if not self.enabled:
            return
        vector = await self.embed(f"{content}")
        if not vector:
            return
        stmt = (
            update(MemoryEntryORM)
            .where(MemoryEntryORM.id == entry_id)
            .values(embedding=vector)
        )
        if db_session is not None:
            await db_session.execute(stmt)
            return
        from app.infrastructure.storage.postgres import get_postgres
        postgres = get_postgres()
        async with postgres.session_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def search_similar(
            self,
            query: str,
            session_id: Optional[str] = None,
            limit: int = 20,
            db_session: Optional[AsyncSession] = None,
    ) -> List[MemoryEntry]:
        if not self.enabled or not query.strip():
            return []
        query_vector = await self.embed(query)
        if not query_vector:
            return []

        # 向量距离检索仍使用 pgvector 运算符（ORM 不直接支持 <=>）
        stmt = text("""
            SELECT id, scope, session_id, title, content, tags, owner_user_id,
                   team_id, source, last_used_at, use_count, created_at, updated_at,
                   embedding <=> :query_vec::vector AS distance
            FROM memory_entries
            WHERE embedding IS NOT NULL
              AND EXISTS (
                SELECT 1
                FROM sessions s
                WHERE s.id = :session_id
                  AND (
                    (s.team_id IS NOT NULL AND memory_entries.team_id = s.team_id)
                    OR (
                      s.team_id IS NULL
                      AND memory_entries.team_id IS NULL
                      AND memory_entries.owner_user_id = s.owner_user_id
                    )
                  )
              )
              AND (
                scope = 'global'
                OR (scope = 'session' AND session_id = :session_id)
              )
            ORDER BY embedding <=> :query_vec::vector
            LIMIT :limit
        """)
        params = {
            "session_id": session_id,
            "query_vec": "[" + ",".join(str(v) for v in query_vector) + "]",
            "limit": limit,
        }
        if db_session is not None:
            result = await db_session.execute(stmt, params)
            rows = result.fetchall()
        else:
            from app.infrastructure.storage.postgres import get_postgres
            postgres = get_postgres()
            async with postgres.session_factory() as session:
                result = await session.execute(stmt, params)
                rows = result.fetchall()

        entries: List[MemoryEntry] = []
        for row in rows:
            entries.append(MemoryEntry(
                id=row.id,
                scope=MemoryScope(row.scope),
                session_id=row.session_id,
                title=row.title,
                content=row.content,
                tags=row.tags or [],
                owner_user_id=row.owner_user_id,
                team_id=row.team_id,
                source=MemorySource(row.source),
                last_used_at=row.last_used_at,
                use_count=row.use_count,
                vector_score=max(0.0, 1.0 - float(row.distance or 0.0)),
                created_at=row.created_at,
                updated_at=row.updated_at,
            ))
        return entries


def get_vector_memory_service() -> VectorMemoryService:
    global _vector_memory_service
    if _vector_memory_service is None:
        _vector_memory_service = VectorMemoryService()
    return _vector_memory_service
