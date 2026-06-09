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
    normalize_usage,
    openai_content_to_gemini_parts,
)

logger = logging.getLogger(__name__)


class GeminiLLM(LLM):
    """Google Gemini via generateContent API."""

    def __init__(self, model: LLMModel, thinking_enabled: bool = False, **kwargs) -> None:
        self._model = model
        self._model_name = model.model_name
        self._temperature = model.temperature
        self._max_tokens = model.max_tokens
        self._supports_multimodal = model.supports_multimodal
        self._capabilities = model.capabilities
        self._base_url = (model.base_url or "https://generativelanguage.googleapis.com").rstrip("/")
        self._api_key = model.api_key
        self._client = httpx.AsyncClient(timeout=300)

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

    def _convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        declarations = []
        for tool in tools or []:
            fn = tool.get("function", {})
            declarations.append({
                "name": fn.get("name"),
                "description": fn.get("description"),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return declarations

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        contents = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                text = content if isinstance(content, str) else json.dumps(content)
                contents.append({"role": "user", "parts": [{"text": f"[system]\n{text}"}]})
            elif role == "user":
                parts = openai_content_to_gemini_parts(content)
                contents.append({"role": "user", "parts": parts})
            elif role == "assistant":
                parts: List[Dict[str, Any]] = []
                text = content if isinstance(content, str) else ""
                if text:
                    parts.append({"text": text})
                for tool_call in msg.get("tool_calls") or []:
                    fn = tool_call.get("function") or {}
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except json.JSONDecodeError:
                        parsed_args = {}
                    parts.append({
                        "functionCall": {
                            "name": fn.get("name"),
                            "args": parsed_args or {},
                        }
                    })
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            elif role == "tool":
                tool_text = content if isinstance(content, str) else json.dumps(content)
                contents.append({
                    "role": "function",
                    "parts": [{
                        "functionResponse": {
                            "name": msg.get("name") or "tool",
                            "response": {"result": tool_text},
                        }
                    }],
                })
        return contents

    async def invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
    ) -> Dict[str, Any]:
        contents = self._convert_messages(messages)
        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": self._temperature,
                "maxOutputTokens": self._max_tokens or 8192,
            },
        }
        if response_format and response_format.get("type") == "json_object":
            payload["generationConfig"]["responseMimeType"] = "application/json"
        if tools:
            payload["tools"] = [{"functionDeclarations": self._convert_tools(tools)}]
        url = f"{self._base_url}/v1beta/models/{self._model_name}:generateContent?key={self._api_key}"
        response = await self._client.post(url, json=payload)
        if response.status_code >= 400:
            raise ServerRequestsError(f"Gemini API error: {response.text}")
        data = response.json()

        candidates = data.get("candidates") or []
        if not candidates:
            raise ServerRequestsError("Gemini API returned no candidates")
        parts = candidates[0].get("content", {}).get("parts") or []
        text_parts = []
        tool_calls = []
        for part in parts:
            if "text" in part:
                text_parts.append(part.get("text", ""))
            function_call = part.get("functionCall")
            if function_call:
                tool_calls.append({
                    "id": f"call_{function_call.get('name', 'tool')}",
                    "type": "function",
                    "function": {
                        "name": function_call.get("name"),
                        "arguments": json.dumps(function_call.get("args") or {}),
                    },
                })
        message: Dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
        if tool_calls:
            message["tool_calls"] = tool_calls
        usage_meta = data.get("usageMetadata") or {}
        usage = normalize_usage({
            "promptTokenCount": usage_meta.get("promptTokenCount"),
            "candidatesTokenCount": usage_meta.get("candidatesTokenCount"),
            "totalTokenCount": usage_meta.get("totalTokenCount"),
        })
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
        contents = self._convert_messages(messages)
        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": self._temperature,
                "maxOutputTokens": self._max_tokens or 8192,
            },
        }
        if response_format and response_format.get("type") == "json_object":
            payload["generationConfig"]["responseMimeType"] = "application/json"
        if tools:
            payload["tools"] = [{"functionDeclarations": self._convert_tools(tools)}]
        url = f"{self._base_url}/v1beta/models/{self._model_name}:streamGenerateContent?alt=sse&key={self._api_key}"

        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        tool_index_by_name: Dict[str, int] = {}
        async with self._client.stream("POST", url, json=payload) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise ServerRequestsError(f"Gemini API error: {body.decode(errors='ignore')}")

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line.removeprefix("data:").strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("忽略无法解析的 Gemini stream chunk: %s", raw[:200])
                    continue

                usage_meta = chunk.get("usageMetadata") or {}
                prompt_tokens = int(usage_meta.get("promptTokenCount") or prompt_tokens)
                completion_tokens = int(usage_meta.get("candidatesTokenCount") or completion_tokens)
                total_tokens = int(usage_meta.get("totalTokenCount") or total_tokens)

                for candidate in chunk.get("candidates") or []:
                    parts = candidate.get("content", {}).get("parts") or []
                    for part in parts:
                        text = part.get("text")
                        if text:
                            yield {"content": text}
                        function_call = part.get("functionCall")
                        if function_call:
                            name = function_call.get("name") or "tool"
                            idx = tool_index_by_name.setdefault(name, len(tool_index_by_name))
                            yield {
                                "tool_calls": [{
                                    "index": idx,
                                    "id": f"call_{name}_{idx}",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(function_call.get("args") or {}),
                                    },
                                }]
                            }

        usage = normalize_usage({
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": completion_tokens,
            "totalTokenCount": total_tokens,
        })
        if usage.get("total_tokens"):
            yield {"usage": usage}
