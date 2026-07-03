#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Union

import httpx

from app.application.errors.exceptions import ServerRequestsError
from app.domain.external.llm import LLM
from app.domain.models.llm_model import LLMModel, ModelCapabilities
from app.infrastructure.external.llm.base_llm import (
    normalize_usage,
    openai_content_to_anthropic_parts,
)

logger = logging.getLogger(__name__)

_SYNTHETIC_RESULT_TOOL = "emit_result"


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

    def _convert_content(self, content: Any) -> Any:
        return openai_content_to_anthropic_parts(content)

    def _convert_messages(self, messages: List[Dict[str, Any]], *, cache_system: bool = True) -> tuple[Any, List[Dict[str, Any]]]:
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
        system_text = "\n".join(system_parts)
        if not system_text:
            return "", converted
        if not cache_system:
            return system_text, converted
        return [{
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }], converted

    def _convert_tools(self, tools: List[Dict[str, Any]], *, cache_tools: bool = True) -> List[Dict[str, Any]]:
        result = []
        for tool in tools or []:
            fn = tool.get("function", {})
            result.append({
                "name": fn.get("name"),
                "description": fn.get("description"),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        if cache_tools and result:
            result[-1]["cache_control"] = {"type": "ephemeral"}
        return result

    def _headers(self, *, prompt_cache: bool = True) -> Dict[str, str]:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if prompt_cache:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"
        return headers

    def _apply_response_schema(
            self,
            payload: Dict[str, Any],
            tools: List[Dict[str, Any]] | None,
            response_schema: Dict[str, Any] | None,
            *,
            cache_tools: bool = True,
    ) -> None:
        if response_schema:
            payload["tools"] = [{
                "name": _SYNTHETIC_RESULT_TOOL,
                "description": "Emit the final structured result.",
                "input_schema": response_schema["schema"],
            }]
            if cache_tools:
                payload["tools"][-1]["cache_control"] = {"type": "ephemeral"}
            payload["tool_choice"] = {"type": "tool", "name": _SYNTHETIC_RESULT_TOOL}
        elif tools:
            payload["tools"] = self._convert_tools(tools, cache_tools=cache_tools)

    @staticmethod
    def _usage_from_response(data: Dict[str, Any]) -> Dict[str, int]:
        return normalize_usage(data.get("usage"))

    async def _post_messages(self, payload: Dict[str, Any], *, prompt_cache: bool = True) -> httpx.Response:
        response = await self._client.post(
            f"{self._base_url}/v1/messages",
            json=payload,
            headers=self._headers(prompt_cache=prompt_cache),
        )
        if response.status_code >= 400 and prompt_cache:
            logger.warning("Anthropic prompt cache request failed, retry without cache headers/blocks")
            fallback_payload = self._fallback_payload_without_prompt_cache(payload)
            return await self._client.post(
                f"{self._base_url}/v1/messages",
                json=fallback_payload,
                headers=self._headers(prompt_cache=False),
            )
        return response

    @staticmethod
    def _without_cache_controls(value: Any) -> Any:
        if isinstance(value, list):
            return [AnthropicLLM._without_cache_controls(item) for item in value]
        if isinstance(value, dict):
            cleaned = {
                key: AnthropicLLM._without_cache_controls(val)
                for key, val in value.items()
                if key != "cache_control"
            }
            return cleaned
        return value

    @staticmethod
    def _fallback_payload_without_prompt_cache(payload: Dict[str, Any]) -> Dict[str, Any]:
        fallback = AnthropicLLM._without_cache_controls(payload)
        system = fallback.get("system")
        if isinstance(system, list):
            text_parts = [
                str(block.get("text") or "")
                for block in system
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            fallback["system"] = "\n".join(part for part in text_parts if part)
        return fallback

    async def invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: Union[str, Dict[str, Any], None] = None,
            response_schema: Dict[str, Any] = None,
            retry_budget: Any = None,
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
        self._apply_response_schema(payload, tools, response_schema)
        if tool_choice and not response_schema:
            payload["tool_choice"] = tool_choice
        response = await self._post_messages(payload)
        if response.status_code >= 400:
            raise ServerRequestsError(f"Anthropic API error: {response.text}")
        data = response.json()

        content_blocks = data.get("content") or []
        text_parts = []
        tool_calls = []
        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use" and block.get("name") == _SYNTHETIC_RESULT_TOOL:
                text_parts.append(json.dumps(block.get("input") or {}, ensure_ascii=False))
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
        usage = self._usage_from_response(data)
        if usage.get("total_tokens"):
            message["_usage"] = usage
        return message

    async def stream_invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: Union[str, Dict[str, Any], None] = None,
            response_schema: Dict[str, Any] = None,
            retry_budget: Any = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        system, converted = self._convert_messages(messages)
        payload: Dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": self._max_tokens or 8192,
            "messages": converted,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        self._apply_response_schema(payload, tools, response_schema)
        if tool_choice and not response_schema:
            payload["tool_choice"] = tool_choice

        prompt_tokens = 0
        completion_tokens = 0
        cache_read_tokens = 0
        cache_creation_tokens = 0
        tool_blocks: Dict[int, Dict[str, Any]] = {}
        synthetic_result_indexes: set[int] = set()
        async with self._client.stream(
                "POST",
                f"{self._base_url}/v1/messages",
                json=payload,
                headers=self._headers(prompt_cache=True),
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                fallback_payload = self._fallback_payload_without_prompt_cache(payload)
                async with self._client.stream(
                        "POST",
                        f"{self._base_url}/v1/messages",
                        json=fallback_payload,
                        headers=self._headers(prompt_cache=False),
                ) as fallback_response:
                    if fallback_response.status_code >= 400:
                        fallback_body = await fallback_response.aread()
                        raise ServerRequestsError(f"Anthropic API error: {fallback_body.decode(errors='ignore')}")
                    async for item in self._iter_stream_lines(fallback_response, response_schema is not None):
                        yield item
                return

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line.removeprefix("data:").strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("忽略无法解析的 Anthropic stream chunk: %s", raw[:200])
                    continue

                event_type = event.get("type")
                if event_type == "message_start":
                    usage = event.get("message", {}).get("usage") or {}
                    prompt_tokens = int(usage.get("input_tokens") or 0)
                    cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)
                    cache_creation_tokens = int(usage.get("cache_creation_input_tokens") or 0)
                elif event_type == "content_block_start":
                    idx = int(event.get("index") or 0)
                    block = event.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        if block.get("name") == _SYNTHETIC_RESULT_TOOL:
                            synthetic_result_indexes.add(idx)
                            continue
                        tool_blocks[idx] = {
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "arguments": "",
                        }
                        yield {
                            "tool_calls": [{
                                "index": idx,
                                "id": block.get("id"),
                                "function": {
                                    "name": block.get("name"),
                                    "arguments": "",
                                },
                            }]
                        }
                elif event_type == "content_block_delta":
                    idx = int(event.get("index") or 0)
                    delta = event.get("delta") or {}
                    delta_type = delta.get("type")
                    if delta_type == "text_delta" and delta.get("text"):
                        yield {"content": delta.get("text")}
                    elif delta_type in {"thinking_delta", "signature_delta"} and delta.get("thinking"):
                        yield {"reasoning_content": delta.get("thinking")}
                    elif delta_type == "input_json_delta":
                        partial_json = delta.get("partial_json") or ""
                        if idx in synthetic_result_indexes:
                            if partial_json:
                                yield {"content": partial_json}
                            continue
                        block = tool_blocks.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        block["arguments"] += partial_json
                        yield {
                            "tool_calls": [{
                                "index": idx,
                                "id": block.get("id"),
                                "function": {
                                    "name": block.get("name"),
                                    "arguments": partial_json,
                                },
                            }]
                        }
                elif event_type == "message_delta":
                    usage = event.get("usage") or {}
                    completion_tokens = int(usage.get("output_tokens") or completion_tokens)
                    stop_reason = (event.get("delta") or {}).get("stop_reason")
                    if stop_reason:
                        yield {"finish_reason": stop_reason}

        usage = normalize_usage({
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "cache_read_input_tokens": cache_read_tokens,
            "cache_creation_input_tokens": cache_creation_tokens,
        })
        if usage.get("total_tokens"):
            yield {"usage": usage}

    async def _iter_stream_lines(self, response: httpx.Response, synthetic_schema: bool):
        prompt_tokens = 0
        completion_tokens = 0
        tool_blocks: Dict[int, Dict[str, Any]] = {}
        synthetic_result_indexes: set[int] = set()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw = line.removeprefix("data:").strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("忽略无法解析的 Anthropic stream chunk: %s", raw[:200])
                continue
            event_type = event.get("type")
            if event_type == "message_start":
                usage = event.get("message", {}).get("usage") or {}
                prompt_tokens = int(usage.get("input_tokens") or 0)
            elif event_type == "content_block_start":
                idx = int(event.get("index") or 0)
                block = event.get("content_block") or {}
                if block.get("type") == "tool_use":
                    if synthetic_schema and block.get("name") == _SYNTHETIC_RESULT_TOOL:
                        synthetic_result_indexes.add(idx)
                        continue
                    tool_blocks[idx] = {"id": block.get("id"), "name": block.get("name"), "arguments": ""}
                    yield {"tool_calls": [{"index": idx, "id": block.get("id"), "function": {"name": block.get("name"), "arguments": ""}}]}
            elif event_type == "content_block_delta":
                idx = int(event.get("index") or 0)
                delta = event.get("delta") or {}
                delta_type = delta.get("type")
                if delta_type == "text_delta" and delta.get("text"):
                    yield {"content": delta.get("text")}
                elif delta_type in {"thinking_delta", "signature_delta"} and delta.get("thinking"):
                    yield {"reasoning_content": delta.get("thinking")}
                elif delta_type == "input_json_delta":
                    partial_json = delta.get("partial_json") or ""
                    if idx in synthetic_result_indexes:
                        if partial_json:
                            yield {"content": partial_json}
                        continue
                    block = tool_blocks.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    block["arguments"] += partial_json
                    yield {"tool_calls": [{"index": idx, "id": block.get("id"), "function": {"name": block.get("name"), "arguments": partial_json}}]}
            elif event_type == "message_delta":
                usage = event.get("usage") or {}
                completion_tokens = int(usage.get("output_tokens") or completion_tokens)
                stop_reason = (event.get("delta") or {}).get("stop_reason")
                if stop_reason:
                    yield {"finish_reason": stop_reason}
        usage = normalize_usage({"input_tokens": prompt_tokens, "output_tokens": completion_tokens})
        if usage.get("total_tokens"):
            yield {"usage": usage}
