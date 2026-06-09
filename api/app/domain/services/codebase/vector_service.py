#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Embedding service for codebase chunks."""
from app.application.services.vector_memory_service import VectorMemoryService


class CodebaseVectorService:
    """Reuse the existing OpenAI-compatible embedding client."""

    def __init__(self) -> None:
        self._vector = VectorMemoryService()

    @property
    def enabled(self) -> bool:
        return self._vector.enabled

    async def embed(self, content: str) -> list[float]:
        return await self._vector.embed(content)
