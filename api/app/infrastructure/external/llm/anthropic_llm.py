#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import logging
from typing import Any, AsyncGenerator, Dict, List

import httpx

from app.application.errors.exceptions import ServerRequestsError
from app.domain.external.llm import LLM
from app.domain.models.llm_model import LLMModel, ModelCapabilities
from app.infrastructure.external.llm.base_llm import (
    invoke_to_stream_deltas,
    normalize_usage,
    openai_content_to_anthropic_parts,
)

logger = logging.getLogger(__name__)


class AnthropicLLM(LLM):
    """Anthropic Claude LLM via Messages API (native tool use + streaming)."""

    def __init__(self, model: LLMModel, thinking_enabled: bool = False, **kwargs) -> None:
        self._model = model
        self._model_name = model.model_name
        self._temperature = model.temperature
        self._max_tokens = model.max_tokens
        self._supports_multimodal = model.supports_multimodal
        self._capabilities = model.capabilities
        self._thinking_enabled = thinking_enabled
        self._base_url = model.base_url.rstrip("/") or "https://api.anthropic.com"
        self._api_key = model.api_key

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @property
    def supports_multimodal(self) -> bool:
        return self._supports_multimodal

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

    def _convert_content(self, content: Any) -> Any:
        return openai_content_to_anthropic_parts(content)

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> tuple[str, List[Dict[str, Any]]]:
        system_parts = []
        converted = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                system_parts.append(content if isinstance(content, str) else json.dumps(content))
            elif role == "user":
                converted.append({"role": "user", "content": self._convert_content(content)})
            elif role == "assistant":
                blocks: List[Dict[str, Any]] = []
                text = content if isinstance(content, str) else ""
                if text:
                    blocks.append({"type": "text", "text": text})
                for tool_call in msg.get("tool_calls") or []:
                    fn = tool_call.get("function") or {}
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except json.JSONDecodeError:
                        parsed_args = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tool_call.get("id"),
                        "name": fn.get("name"),
                        "input": parsed_args or {},
                    })
                converted.append({
                    "role": "assistant",
                    "content": blocks if blocks else (content or ""),
                })
            elif role == "tool":
                tool_content = content if isinstance(content, str) else json.dumps(content)
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id"),
                        "content": tool_content,
                    }],
                })
        return "\n".join(system_parts), converted

    def _convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result = []
        for tool in tools or []:
            fn = tool.get("function", {})
            result.append({
                "name": fn.get("name"),
                "description": fn.get("description"),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return result

    async def invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
    ) -> Dict[str, Any]:
        system, converted = self._convert_messages(messages)
        payload: Dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": self._max_tokens or 8192,
            "messages": converted,
        }
        if system:
            payload["system"] = system
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        if tools:
            payload["tools"] = self._convert_tools(tools)
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(f"{self._base_url}/v1/messages", json=payload, headers=headers)
            if response.status_code >= 400:
                raise ServerRequestsError(f"Anthropic API error: {response.text}")
            data = response.json()

        content_blocks = data.get("content") or []
        text_parts = []
        tool_calls = []
        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id"),
                    "type": "function",
                    "function": {
                        "name": block.get("name"),
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                })
        message = {"role": "assistant", "content": "".join(text_parts) or None}
        if tool_calls:
            message["tool_calls"] = tool_calls
        usage = normalize_usage(data.get("usage"))
        if usage.get("total_tokens"):
            message["_usage"] = usage
        return message

    async def stream_invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        message = await self.invoke(messages, tools, response_format, tool_choice)
        async for delta in invoke_to_stream_deltas(message):
            yield delta
