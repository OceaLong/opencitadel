#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Vector memory with pgvector + OpenAI-compatible embeddings."""
import logging
from typing import List, Optional

from openai import AsyncOpenAI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.memory_entry import MemoryEntry, MemoryScope, MemorySource
from core.config import get_settings

logger = logging.getLogger(__name__)
_EMBEDDING_DIM = 1536


class VectorMemoryService:
    """pgvector-backed semantic memory recall."""

    def __init__(self) -> None:
        settings = get_settings()
        self.enabled = settings.memory_vector_enabled
        self.embedding_provider = settings.embedding_provider
        self.embedding_model = settings.embedding_model
        self._openai_api_key = settings.embedding_api_key or ""
        self._openai_base_url = settings.embedding_base_url or "https://api.openai.com/v1"

    def _client(self) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=self._openai_api_key or "sk-placeholder",
            base_url=self._openai_base_url,
        )

    async def embed(self, content: str) -> List[float]:
        if not self.enabled or not content.strip():
            return []
        client = self._client()
        response = await client.embeddings.create(
            model=self.embedding_model,
            input=content,
        )
        return response.data[0].embedding

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
        vector_literal = "[" + ",".join(str(v) for v in vector) + "]"
        stmt = text("UPDATE memory_entries SET embedding = :embedding::vector WHERE id = :id")
        params = {"embedding": vector_literal, "id": entry_id}
        if db_session is not None:
            await db_session.execute(stmt, params)
            return
        from app.infrastructure.storage.postgres import get_postgres
        postgres = get_postgres()
        async with postgres.session_factory() as session:
            await session.execute(stmt, params)
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

        stmt = text("""
            SELECT id, scope, session_id, title, content, tags, source,
                   last_used_at, use_count, created_at, updated_at
            FROM memory_entries
            WHERE embedding IS NOT NULL
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
                source=MemorySource(row.source),
                last_used_at=row.last_used_at,
                use_count=row.use_count,
                created_at=row.created_at,
                updated_at=row.updated_at,
            ))
        return entries
