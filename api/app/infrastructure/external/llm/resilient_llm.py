#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Resilient LLM wrapper: single retry authority, breaker, guarded fallback."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

from app.application.errors.exceptions import ServerRequestsError
from app.application.services.config_provider import get_runtime_config
from app.application.services.llm_model_service import LLMModelService
from app.domain.external.llm import LLM
from app.domain.models.error_codes import MODEL_NOT_CONFIGURED, MODEL_UNAVAILABLE
from app.domain.models.llm_model import LLMModel, LLMProvider
from app.domain.utils.llm_retry import (
    classify_llm_error_code,
    is_retriable_llm_error,
)
from app.infrastructure.external.llm.circuit_breaker import get_llm_circuit_breaker
from app.infrastructure.external.llm.base_llm import (
    _has_multimodal_image_content,
    _strip_multimodal_to_text,
    is_retriable_multimodal_error,
)
from app.infrastructure.external.llm.factory import LLMFactory
from app.infrastructure.observability.llm_metrics import record_llm_resilience_event

logger = logging.getLogger(__name__)


class ModelUnavailableError(ServerRequestsError):
    """Raised when circuit is open or no invokable model remains."""

    def __init__(self, msg: str = "模型服务暂不可用", *, error_code: str = MODEL_UNAVAILABLE) -> None:
        super().__init__(msg)
        self.error_code = error_code


class ResilientLLMClient:
    """Wraps a concrete LLM with resilience policies."""

    def __init__(
            self,
            inner: LLM,
            model: LLMModel,
            *,
            llm_model_service: Optional[LLMModelService] = None,
            thinking_enabled: bool = False,
    ) -> None:
        self._inner = inner
        self._model = model
        self._llm_model_service = llm_model_service
        self._thinking_enabled = thinking_enabled
        self._streaming_started = False
        self._candidate_cache: dict[bool, List[LLMModel]] = {}
        self._breaker = get_llm_circuit_breaker()

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    @property
    def temperature(self) -> float:
        return self._inner.temperature

    @property
    def max_tokens(self) -> int:
        return self._inner.max_tokens

    @property
    def supports_multimodal(self) -> bool:
        return self._inner.supports_multimodal

    @property
    def capabilities(self):
        return self._inner.capabilities

    @property
    def model_id(self) -> str:
        return self._model.id

    @property
    def streaming_started(self) -> bool:
        return self._streaming_started

    def _config(self):
        return get_runtime_config().model_resilience

    async def invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: Union[str, Dict[str, Any], None] = None,
            response_schema: Dict[str, Any] = None,
            retry_budget: Any = None,
    ) -> Dict[str, Any]:
        cfg = self._config()
        deadline = time.monotonic() + cfg.max_call_budget_seconds
        last_error: Optional[Exception] = None
        candidates = await self._build_candidate_chain(require_vision=self._needs_vision(messages))
        if not candidates:
            raise ModelUnavailableError("未配置可用模型", error_code=MODEL_NOT_CONFIGURED)

        for candidate_idx, candidate in enumerate(candidates):
            if not await self._candidate_allowed(candidate):
                last_error = ModelUnavailableError(
                    f"模型「{candidate.display_name}」熔断开路，请稍后重试",
                    error_code=MODEL_UNAVAILABLE,
                )
                continue
            attempts = 0
            while attempts < cfg.max_attempts_per_call and time.monotonic() < deadline:
                attempts += 1
                client = self._client_for(candidate)
                try:
                    result = await client.invoke(
                        messages,
                        tools,
                        response_format,
                        tool_choice,
                        response_schema=response_schema,
                        retry_budget=retry_budget,
                    )
                    await self._breaker.record_success(candidate.id)
                    if candidate.id != self._model.id:
                        record_llm_resilience_event("fallback_success", candidate.id, candidate.provider.value)
                    return result
                except Exception as exc:
                    last_error = exc
                    await self._breaker.record_failure(candidate.id, exc)
                    record_llm_resilience_event("invoke_error", candidate.id, candidate.provider.value)
                    if not is_retriable_llm_error(exc):
                        raise ModelUnavailableError(str(exc), error_code=classify_llm_error_code(exc)) from exc
                    if attempts >= cfg.max_attempts_per_call:
                        break
                    if retry_budget is not None:
                        retry_budget.consume("resilient_invoke_retry")
                    delay = min(2 ** (attempts - 1), 8)
                    await asyncio.sleep(delay)
            if not cfg.fallback_enabled:
                break
            if candidate_idx + 1 < len(candidates) and retry_budget is not None:
                retry_budget.consume("resilient_invoke_fallback")

        code = classify_llm_error_code(last_error) if last_error else MODEL_UNAVAILABLE
        raise ModelUnavailableError(str(last_error) if last_error else "模型调用失败", error_code=code)

    async def stream_invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: Union[str, Dict[str, Any], None] = None,
            response_schema: Dict[str, Any] = None,
            retry_budget: Any = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        self._streaming_started = False
        cfg = self._config()
        deadline = time.monotonic() + cfg.max_call_budget_seconds
        last_error: Optional[Exception] = None
        candidates = await self._build_candidate_chain(require_vision=self._needs_vision(messages))
        if not candidates:
            raise ModelUnavailableError("未配置可用模型", error_code=MODEL_NOT_CONFIGURED)

        for candidate_idx, candidate in enumerate(candidates):
            if self._streaming_started:
                break
            if not await self._candidate_allowed(candidate):
                last_error = ModelUnavailableError(
                    f"模型「{candidate.display_name}」熔断开路，请稍后重试",
                    error_code=MODEL_UNAVAILABLE,
                )
                continue
            attempts = 0
            stripped_for_multimodal = False
            request_messages = messages
            while attempts < cfg.max_attempts_per_call and time.monotonic() < deadline:
                attempts += 1
                client = self._client_for(candidate)
                try:
                    async for chunk in client.stream_invoke(
                        request_messages,
                        tools,
                        response_format,
                        tool_choice,
                        response_schema=response_schema,
                        retry_budget=retry_budget,
                    ):
                        self._streaming_started = True
                        yield chunk
                    await self._breaker.record_success(candidate.id)
                    return
                except Exception as exc:
                    last_error = exc
                    if self._streaming_started:
                        code = classify_llm_error_code(exc)
                        raise ModelUnavailableError(str(exc), error_code=code) from exc
                    if (
                            not stripped_for_multimodal
                            and _has_multimodal_image_content(messages)
                            and is_retriable_multimodal_error(exc)
                    ):
                        stripped_for_multimodal = True
                        request_messages = _strip_multimodal_to_text(messages)
                        logger.warning("多模态流式请求失败，降级为文本后重试: error=%s", exc)
                        attempts -= 1
                        continue
                    await self._breaker.record_failure(candidate.id, exc)
                    if not is_retriable_llm_error(exc):
                        raise ModelUnavailableError(str(exc), error_code=classify_llm_error_code(exc)) from exc
                    if attempts >= cfg.max_attempts_per_call:
                        break
                    if retry_budget is not None:
                        retry_budget.consume("resilient_stream_invoke_retry")
                    delay = min(2 ** (attempts - 1), 8)
                    await asyncio.sleep(delay)
            if not cfg.fallback_enabled or self._streaming_started:
                break
            if candidate_idx + 1 < len(candidates) and retry_budget is not None:
                retry_budget.consume("resilient_stream_invoke_fallback")

        code = classify_llm_error_code(last_error) if last_error else MODEL_UNAVAILABLE
        raise ModelUnavailableError(str(last_error) if last_error else "模型流式调用失败", error_code=code)

    def _client_for(self, model: LLMModel) -> LLM:
        if model.id == self._model.id:
            return self._inner
        return LLMFactory.create(model, thinking_enabled=self._thinking_enabled)

    async def _build_candidate_chain(self, *, require_vision: bool) -> List[LLMModel]:
        if require_vision in self._candidate_cache:
            return list(self._candidate_cache[require_vision])
        chain: List[LLMModel] = [self._model]
        if not self._config().fallback_enabled or not self._llm_model_service:
            self._candidate_cache[require_vision] = chain
            return chain
        try:
            all_models = await self._llm_model_service.list_models(mask=False)
        except Exception:
            self._candidate_cache[require_vision] = chain
            return chain
        for candidate in all_models:
            if candidate.id == self._model.id:
                continue
            if candidate.provider != self._model.provider:
                if self._config().allow_cross_provider_fallback:
                    pass
                else:
                    continue
            caps = candidate.capabilities
            if require_vision and not (caps.vision or candidate.supports_multimodal):
                continue
            if candidate.provider != LLMProvider.OLLAMA and not candidate.api_key.strip():
                continue
            chain.append(candidate)
        self._candidate_cache[require_vision] = chain
        return chain

    async def _candidate_allowed(self, candidate: LLMModel) -> bool:
        cfg = self._config()
        if not cfg.enabled or not cfg.fast_fail_on_open_circuit:
            return True
        decision = await self._breaker.allow_request(candidate.id)
        if decision == "deny":
            record_llm_resilience_event(
                "circuit_open_fast_fail",
                candidate.id,
                candidate.provider.value,
            )
            return False
        return True

    @staticmethod
    def _needs_vision(messages: List[Dict[str, Any]]) -> bool:
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        return True
        return False


def create_resilient_llm(
        model: LLMModel,
        *,
        thinking_enabled: bool = False,
        llm_model_service: Optional[LLMModelService] = None,
) -> ResilientLLMClient:
    inner = LLMFactory.create(model, thinking_enabled=thinking_enabled)
    return ResilientLLMClient(
        inner,
        model,
        llm_model_service=llm_model_service,
        thinking_enabled=thinking_enabled,
    )
