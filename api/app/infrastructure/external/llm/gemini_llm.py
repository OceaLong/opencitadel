#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import logging
from typing import Any, AsyncGenerator, Dict, List

import httpx

from app.application.errors.exceptions import ServerRequestsError
from app.domain.external.llm import LLM
from app.domain.models.llm_model import LLMModel, ModelCapabilities
from app.infrastructure.external.llm.base_llm import invoke_to_stream_deltas

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

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        contents = []
        for msg in messages:
            role = msg.get("role")
            text = msg.get("content")
            if isinstance(text, list):
                text = json.dumps(text)
            if role == "system":
                contents.append({"role": "user", "parts": [{"text": f"[system]\n{text}"}]})
            elif role in {"user", "assistant"}:
                gemini_role = "user" if role == "user" else "model"
                contents.append({"role": gemini_role, "parts": [{"text": text or ""}]})
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
        url = f"{self._base_url}/v1beta/models/{self._model_name}:generateContent?key={self._api_key}"
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(url, json=payload)
            if response.status_code >= 400:
                raise ServerRequestsError(f"Gemini API error: {response.text}")
            data = response.json()

        candidates = data.get("candidates") or []
        if not candidates:
            raise ServerRequestsError("Gemini API returned no candidates")
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts)
        return {"role": "assistant", "content": text}

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
