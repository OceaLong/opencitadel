#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Embedding service for knowledge-base chunks."""
from app.application.services.config_provider import get_runtime_config
from app.application.services.vector_memory_service import get_vector_memory_service


class KBVectorService:
    def __init__(self) -> None:
        self._vector = get_vector_memory_service()
        runtime = get_runtime_config()
        self.enabled = (
            runtime.knowledge_base.vector_enabled
            and runtime.feature_flags.enable_embeddings
        )

    async def embed(self, content: str) -> list[float]:
        if not self.enabled or not content.strip():
            return []
        vectors = await self._vector.embed_batch_unconditional([content])
        return vectors[0] if vectors else []

    async def embed_batch(self, contents: list[str]) -> list[list[float]]:
        if not self.enabled:
            return [[] for _ in contents]
        return await self._vector.embed_batch_unconditional(contents)
