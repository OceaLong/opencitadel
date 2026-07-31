#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Version-closed hybrid retrieval for codebase chunks."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional

from app.domain.models.codebase import CodebaseChunk, CodebaseFile, CodebaseSymbol
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.codebase.vector_service import CodebaseVectorService

logger = logging.getLogger(__name__)

EMBEDDING_UNAVAILABLE = "EMBEDDING_UNAVAILABLE"


@dataclass(frozen=True)
class CodeSearchResult:
    version_id: str
    path: str
    lines: tuple[int, int]
    symbol_id: Optional[str]
    sources: tuple[str, ...]
    score: float
    content: str = ""


@dataclass(frozen=True)
class CodeSearchResponse:
    items: list[CodeSearchResult] = field(default_factory=list)
    capabilities: dict[str, bool] = field(default_factory=dict)
    degraded_reasons: list[str] = field(default_factory=list)


class HybridCodeRetriever:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            *,
            vector_service: Optional[CodebaseVectorService] = None,
            rrf_k: int = 60,
    ) -> None:
        self._uow_factory = uow_factory
        self.vector = vector_service or CodebaseVectorService()
        self._rrf_k = rrf_k

    async def retrieve(
            self,
            codebase_id: str,
            version_id: str,
            query: str,
            limit: int = 5,
    ) -> CodeSearchResponse:
        if not str(codebase_id or "").strip():
            raise ValueError("codebase id is required")
        if not str(version_id or "").strip():
            raise ValueError("codebase version id is required")
        if limit < 1:
            raise ValueError("retrieval limit must be positive")

        fetch_limit = max(limit * 3, limit)
        async with self._uow_factory() as uow:
            lexical_hits = await uow.codebase.search_lexical(
                codebase_id,
                version_id,
                query,
                limit=fetch_limit,
            )

        capabilities = {
            "lexical_search": True,
            "vector_search": True,
        }
        degraded_reasons: list[str] = []
        vector_hits: list[tuple[CodebaseChunk, float]] = []
        try:
            if getattr(self.vector, "enabled", True) is False:
                raise ValueError("embedding service disabled")
            embedding = await self.vector.embed(query)
            if not self._valid_embedding(embedding):
                raise ValueError("embedding is empty or malformed")
            async with self._uow_factory() as uow:
                vector_hits = await uow.codebase.search_vector(
                    codebase_id,
                    version_id,
                    [float(item) for item in embedding],
                    limit=fetch_limit,
                )
        except Exception:
            logger.warning(
                "代码库向量检索不可用，降级为 lexical-only codebase=%s version=%s",
                codebase_id,
                version_id,
                exc_info=True,
            )
            capabilities["vector_search"] = False
            degraded_reasons.append(EMBEDDING_UNAVAILABLE)

        fused = self._rrf_fuse(
            codebase_id,
            version_id,
            ("lexical", lexical_hits),
            ("vector", vector_hits),
        )
        if not fused:
            return CodeSearchResponse(
                items=[],
                capabilities=capabilities,
                degraded_reasons=self._dedupe(degraded_reasons),
            )

        async with self._uow_factory() as uow:
            files = {
                file.id: file
                for file in await uow.codebase.list_files(
                    codebase_id,
                    version_id=version_id,
                )
            }
            symbol_ids = [
                chunk.symbol_id
                for chunk, _score, _sources in fused
                if chunk.symbol_id
            ]
            symbols = {
                symbol.id: symbol
                for symbol in await uow.codebase.list_symbols_by_ids(
                    codebase_id,
                    list(dict.fromkeys(symbol_ids)),
                    version_id=version_id,
                )
            }

        items: list[CodeSearchResult] = []
        for chunk, score, sources in fused[:limit]:
            file = files.get(chunk.file_id or "")
            symbol = symbols.get(chunk.symbol_id or "")
            items.append(
                CodeSearchResult(
                    version_id=version_id,
                    path=self._path_for(chunk, file),
                    lines=self._lines_for(symbol),
                    symbol_id=chunk.symbol_id,
                    sources=tuple(sources),
                    score=score,
                    content=chunk.content,
                )
            )
        return CodeSearchResponse(
            items=items,
            capabilities=capabilities,
            degraded_reasons=self._dedupe(degraded_reasons),
        )

    def _rrf_fuse(
            self,
            codebase_id: str,
            version_id: str,
            *ranked_lists: tuple[str, list[tuple[CodebaseChunk, float]]],
    ) -> list[tuple[CodebaseChunk, float, list[str]]]:
        by_id: dict[str, tuple[CodebaseChunk, float, list[str]]] = {}
        for source, ranked in ranked_lists:
            for rank, (chunk, raw_score) in enumerate(ranked, start=1):
                if chunk.codebase_id != codebase_id or chunk.version_id != version_id:
                    continue
                fused_score = 1.0 / (self._rrf_k + rank)
                fused_score += float(raw_score or 0) * 0.001
                existing = by_id.get(chunk.id)
                if existing is None:
                    by_id[chunk.id] = (chunk, fused_score, [source])
                    continue
                existing_chunk, existing_score, sources = existing
                if source not in sources:
                    sources.append(source)
                by_id[chunk.id] = (
                    existing_chunk,
                    existing_score + fused_score,
                    sources,
                )
        return sorted(
            by_id.values(),
            key=lambda item: (-item[1], item[0].id),
        )

    @staticmethod
    def _valid_embedding(embedding: object) -> bool:
        return (
            isinstance(embedding, list)
            and bool(embedding)
            and all(isinstance(item, (int, float)) for item in embedding)
        )

    @staticmethod
    def _dedupe(reasons: list[str]) -> list[str]:
        return list(dict.fromkeys(reason for reason in reasons if reason))

    @staticmethod
    def _path_for(
        chunk: CodebaseChunk,
        file: Optional[CodebaseFile],
    ) -> str:
        return file.path if file is not None else "?"

    @staticmethod
    def _lines_for(symbol: Optional[CodebaseSymbol]) -> tuple[int, int]:
        if symbol is None:
            return (0, 0)
        return (symbol.start_line or 0, symbol.end_line or 0)
