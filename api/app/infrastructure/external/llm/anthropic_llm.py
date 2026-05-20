#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import List, Dict, Any

from app.application.errors.exceptions import ServerRequestsError
from app.domain.external.llm import LLM
from app.domain.models.llm_model import LLMModel

logger = logging.getLogger(__name__)


class AnthropicLLM(LLM):
    """Anthropic Claude LLM实现（stub，待完整实现）"""

    def __init__(self, model: LLMModel, **kwargs) -> None:
        self._model = model
        self._model_name = model.model_name
        self._temperature = model.temperature
        self._max_tokens = model.max_tokens

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    async def invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
    ) -> Dict[str, Any]:
        logger.error("Anthropic Provider尚未完整实现，请安装anthropic SDK并完成适配")
        raise ServerRequestsError(
            "Anthropic Provider尚未完整实现。请使用OpenAI/Ollama/Azure Provider，或等待后续版本支持。"
        )
