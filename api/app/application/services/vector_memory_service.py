#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tier 3 向量记忆服务占位。
当 memory_vector_enabled=True 时，可在此接入 pgvector + embedding 实现余弦相似度召回。
当前版本保持关闭，召回逻辑由 MemoryService.recall_for_session 的时间衰减策略处理。
"""
import logging
from core.config import get_settings

logger = logging.getLogger(__name__)


class VectorMemoryService:
    """向量记忆服务占位实现"""

    def __init__(self) -> None:
        settings = get_settings()
        self.enabled = settings.memory_vector_enabled
        self.embedding_provider = settings.embedding_provider
        self.embedding_model = settings.embedding_model

    async def embed(self, text: str) -> list:
        if not self.enabled:
            return []
        logger.warning("向量记忆已配置启用但尚未实现，请接入 pgvector")
        return []

    async def search_similar(self, query: str, limit: int = 20) -> list:
        if not self.enabled:
            return []
        return []
