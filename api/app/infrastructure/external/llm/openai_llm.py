#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import List, Dict, Any, Union

from openai import AsyncOpenAI

from app.application.errors.exceptions import ServerRequestsError
from app.domain.external.llm import LLM
from app.domain.models.app_config import LLMConfig
from app.domain.models.llm_model import LLMModel

logger = logging.getLogger(__name__)


class OpenAILLM(LLM):
    """基于OpenAI SDK/兼容OpenAI格式的LLM调用类（支持OpenAI/Ollama/Azure）"""

    def __init__(self, config: Union[LLMModel, LLMConfig], **kwargs) -> None:
        if isinstance(config, LLMModel):
            base_url = config.base_url
            api_key = config.api_key
            model_name = config.model_name
            temperature = config.temperature
            max_tokens = config.max_tokens
            extra = config.extra_params or {}
        else:
            base_url = str(config.base_url)
            api_key = config.api_key
            model_name = config.model_name
            temperature = config.temperature
            max_tokens = config.max_tokens
            extra = {}

        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "sk-placeholder",
            **{k: v for k, v in extra.items() if k not in ("base_url", "api_key")},
            **kwargs,
        )
        self._model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = 3600

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
        request_kwargs: Dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "timeout": self._timeout,
        }
        if self._temperature is not None:
            request_kwargs["temperature"] = self._temperature
        if self._max_tokens is not None and self._max_tokens > 0:
            request_kwargs["max_tokens"] = self._max_tokens
        if response_format is not None:
            request_kwargs["response_format"] = response_format

        try:
            if tools:
                logger.info(f"调用OpenAI客户端向LLM发起请求并携带工具信息: {self._model_name}")
                response = await self._client.chat.completions.create(
                    **request_kwargs,
                    tools=tools,
                    tool_choice=tool_choice,
                    parallel_tool_calls=False,
                )
            else:
                logger.info(f"调用OpenAI客户端向LLM发起请求未携带工具: {self._model_name}")
                response = await self._client.chat.completions.create(**request_kwargs)
            logger.info(f"OpenAI客户端返回内容: {response.model_dump()}")
            return response.choices[0].message.model_dump()
        except Exception as e:
            logger.error(f"调用OpenAI客户端发生错误: {str(e)}", exc_info=True)
            raise ServerRequestsError(f"调用LLM失败: {str(e)}")
