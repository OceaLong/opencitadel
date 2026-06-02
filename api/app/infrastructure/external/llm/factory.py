#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging

from app.domain.external.llm import LLM
from app.domain.models.llm_model import LLMModel, LLMProvider
from app.infrastructure.external.llm.anthropic_llm import AnthropicLLM
from app.infrastructure.external.llm.gemini_llm import GeminiLLM
from app.infrastructure.external.llm.openai_llm import OpenAILLM

logger = logging.getLogger(__name__)


class LLMFactory:
    """LLM工厂，根据模型配置创建对应Provider实现"""

    @staticmethod
    def create(model: LLMModel, thinking_enabled: bool = False) -> LLM:
        provider = model.provider
        if provider in (LLMProvider.OPENAI, LLMProvider.OLLAMA, LLMProvider.AZURE):
            return OpenAILLM(model, thinking_enabled=thinking_enabled)
        if provider == LLMProvider.ANTHROPIC:
            return AnthropicLLM(model, thinking_enabled=thinking_enabled)
        if provider == LLMProvider.GEMINI:
            return GeminiLLM(model, thinking_enabled=thinking_enabled)
        raise ValueError(f"不支持的LLM Provider: {provider}")
