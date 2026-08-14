#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Embedding service for codebase chunks."""
from app.domain.vector_port import get_vector_memory


class CodebaseVectorService:
    """Reuse the existing OpenAI-compatible embedding client."""

    def __init__(self) -> None:
        self._vector = get_vector_memory()

    @property
    def enabled(self) -> bool:
        return self._vector.enabled

    async def embed(self, content: str) -> list[float]:
        return await self._vector.embed(content)

    async def embed_batch(self, contents: list[str]) -> list[list[float]]:
        return await self._vector.embed_batch(contents)
