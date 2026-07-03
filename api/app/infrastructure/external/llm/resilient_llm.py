#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Resilient LLM wrapper: single retry authority, breaker, guarded fallback."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union

from app.application.errors.exceptions import ServerRequestsError
from app.application.services.config_provider import get_runtime_config
from app.application.services.llm_model_service import LLMModelService
from app.domain.external.llm import LLM
from app.domain.models.app_config import ModelResilienceConfig
from app.domain.models.error_codes import MODEL_NOT_CONFIGURED, MODEL_QUOTA_EXCEEDED, MODEL_UNAVAILABLE
from app.domain.models.llm_model import LLMModel, LLMProvider
from app.domain.utils.llm_retry import (
    classify_llm_error_code,
    is_quota_exhausted_error,
    is_quota_fallback_eligible,
    is_retriable_llm_error,
)
from app.infrastructure.external.llm.circuit_breaker import get_llm_circuit_breaker
from app.infrastructure.external.llm.base_llm import (
    _has_multimodal_image_content,
    _strip_multimodal_to_text,
    is_retriable_multimodal_error,
)
from app.domain.models.event import AssistantNoticeEvent
from app.infrastructure.external.llm.factory import LLMFactory
from app.infrastructure.observability.llm_metrics import record_llm_resilience_event

logger = logging.getLogger(__name__)

_THINKING_PARAM_KEYS = frozenset({"thinking_request_params", "thinking_extra_body"})


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
            resilience_config: Optional[ModelResilienceConfig] = None,
    ) -> None:
        self._inner = inner
        self._model = model
        self._active_model = model
        self._llm_model_service = llm_model_service
        self._thinking_enabled = thinking_enabled
        self._resilience_config = resilience_config
        self._streaming_started = False
        self._candidate_cache: dict[Tuple[bool, bool, bool, bool, bool], List[LLMModel]] = {}
        self._breaker = get_llm_circuit_breaker()
        self._quota_exhausted_model_ids: set[str] = set()
        self._notified_active_model_id: Optional[str] = None
        self._fallback_clients: dict[str, LLM] = {}

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
    def active_model(self) -> LLMModel:
        return self._active_model

    @property
    def streaming_started(self) -> bool:
        return self._streaming_started

    @staticmethod
    def _thinking_enabled_for(model: LLMModel, session_thinking: bool) -> bool:
        if not session_thinking:
            return False
        extra = model.extra_params or {}
        return any(key in extra for key in _THINKING_PARAM_KEYS)

    def consume_fallback_notice_event(self) -> Optional[AssistantNoticeEvent]:
        """Emit at most one assistant notice per fallback target model per task."""
        active = self._active_model
        if active.id == self._model.id:
            return None
        if active.id == self._notified_active_model_id:
            return None
        self._notified_active_model_id = active.id
        return AssistantNoticeEvent(
            message="",
            i18n_key="sessionDetail.modelFallbackNotice",
            i18n_params={"modelName": active.display_name},
        )

    def _config(self) -> ModelResilienceConfig:
        if self._resilience_config is not None:
            return self._resilience_config
        return get_runtime_config().model_resilience

    def _mark_quota_exhausted(self, model_id: str) -> None:
        self._quota_exhausted_model_ids.add(model_id)

    def _should_skip_quota_exhausted(self, candidate: LLMModel) -> bool:
        return candidate.id in self._quota_exhausted_model_ids

    def _all_candidates_quota_exhausted(self, candidates: List[LLMModel]) -> bool:
        if not self._quota_exhausted_model_ids:
            return False
        return all(candidate.id in self._quota_exhausted_model_ids for candidate in candidates)

    def _build_final_error(
            self,
            last_error: Optional[Exception],
            candidates: List[LLMModel],
            *,
            streaming: bool,
    ) -> ModelUnavailableError:
        if self._all_candidates_quota_exhausted(candidates):
            msg = str(last_error) if last_error else "所有已配置模型 API 配额已耗尽"
            return ModelUnavailableError(msg, error_code=MODEL_QUOTA_EXCEEDED)
        if last_error and is_quota_exhausted_error(last_error):
            code = MODEL_QUOTA_EXCEEDED
        else:
            code = classify_llm_error_code(last_error) if last_error else MODEL_UNAVAILABLE
        default_msg = "模型流式调用失败" if streaming else "模型调用失败"
        msg = str(last_error) if last_error else default_msg
        return ModelUnavailableError(msg, error_code=code)

    def _maybe_consume_fallback_budget(
            self,
            retry_budget: Any,
            error: Optional[Exception],
            cfg: ModelResilienceConfig,
            *,
            streaming: bool,
    ) -> None:
        if retry_budget is None or error is None:
            return
        if is_quota_fallback_eligible(error):
            return
        if is_retriable_llm_error(error) and cfg.fallback_enabled:
            reason = "resilient_stream_invoke_fallback" if streaming else "resilient_invoke_fallback"
            retry_budget.consume(reason)

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
            if self._should_skip_quota_exhausted(candidate):
                continue
            if not await self._candidate_allowed(candidate):
                last_error = ModelUnavailableError(
                    f"模型「{candidate.display_name}」熔断开路，请稍后重试",
                    error_code=MODEL_UNAVAILABLE,
                )
                continue
            attempts = 0
            candidate_error: Optional[Exception] = None
            while attempts < cfg.max_attempts_per_call:
                if attempts > 0 and time.monotonic() >= deadline:
                    break
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
                    self._active_model = candidate
                    if candidate.id != self._model.id:
                        record_llm_resilience_event("fallback_success", candidate.id, candidate.provider.value)
                    return result
                except Exception as exc:
                    last_error = exc
                    candidate_error = exc
                    await self._breaker.record_failure(candidate.id, exc)
                    record_llm_resilience_event("invoke_error", candidate.id, candidate.provider.value)
                    if cfg.fallback_on_quota_exceeded and is_quota_fallback_eligible(exc):
                        self._mark_quota_exhausted(candidate.id)
                        break
                    if is_retriable_llm_error(exc):
                        if attempts >= cfg.max_attempts_per_call:
                            break
                        if retry_budget is not None:
                            retry_budget.consume("resilient_invoke_retry")
                        delay = min(2 ** (attempts - 1), 8)
                        await asyncio.sleep(delay)
                        continue
                    raise ModelUnavailableError(str(exc), error_code=classify_llm_error_code(exc)) from exc
            if not self._can_advance_to_next_candidate(cfg, candidate_error or last_error, candidate_idx, candidates):
                break
            self._maybe_consume_fallback_budget(retry_budget, candidate_error or last_error, cfg, streaming=False)

        raise self._build_final_error(last_error, candidates, streaming=False)

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
            if self._should_skip_quota_exhausted(candidate):
                continue
            if not await self._candidate_allowed(candidate):
                last_error = ModelUnavailableError(
                    f"模型「{candidate.display_name}」熔断开路，请稍后重试",
                    error_code=MODEL_UNAVAILABLE,
                )
                continue
            attempts = 0
            stripped_for_multimodal = False
            request_messages = messages
            candidate_error: Optional[Exception] = None
            while attempts < cfg.max_attempts_per_call:
                if attempts > 0 and time.monotonic() >= deadline:
                    break
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
                        self._active_model = candidate
                        yield chunk
                    await self._breaker.record_success(candidate.id)
                    if candidate.id != self._model.id:
                        record_llm_resilience_event("fallback_success", candidate.id, candidate.provider.value)
                    return
                except Exception as exc:
                    last_error = exc
                    candidate_error = exc
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
                    if cfg.fallback_on_quota_exceeded and is_quota_fallback_eligible(exc):
                        self._mark_quota_exhausted(candidate.id)
                        break
                    if is_retriable_llm_error(exc):
                        if attempts >= cfg.max_attempts_per_call:
                            break
                        if retry_budget is not None:
                            retry_budget.consume("resilient_stream_invoke_retry")
                        delay = min(2 ** (attempts - 1), 8)
                        await asyncio.sleep(delay)
                        continue
                    raise ModelUnavailableError(str(exc), error_code=classify_llm_error_code(exc)) from exc
            if self._streaming_started:
                break
            if not self._can_advance_to_next_candidate(cfg, candidate_error or last_error, candidate_idx, candidates):
                break
            self._maybe_consume_fallback_budget(retry_budget, candidate_error or last_error, cfg, streaming=True)

        raise self._build_final_error(last_error, candidates, streaming=True)

    def _can_advance_to_next_candidate(
            self,
            cfg,
            error: Optional[Exception],
            candidate_idx: int,
            candidates: List[LLMModel],
    ) -> bool:
        if error is None or candidate_idx + 1 >= len(candidates):
            return False
        if is_quota_fallback_eligible(error) and cfg.fallback_on_quota_exceeded:
            return True
        if is_retriable_llm_error(error) and cfg.fallback_enabled:
            return True
        if is_retriable_llm_error(error) and cfg.fallback_on_quota_exceeded:
            return True
        return False

    def _client_for(self, model: LLMModel) -> LLM:
        if model.id == self._model.id:
            return self._inner
        cached = self._fallback_clients.get(model.id)
        if cached is not None:
            return cached
        client = LLMFactory.create(
            model,
            thinking_enabled=self._thinking_enabled_for(model, self._thinking_enabled),
        )
        self._fallback_clients[model.id] = client
        return client

    async def _build_candidate_chain(self, *, require_vision: bool) -> List[LLMModel]:
        cfg = self._config()
        cache_key = (
            require_vision,
            self._thinking_enabled,
            cfg.fallback_enabled,
            cfg.fallback_on_quota_exceeded,
            cfg.allow_cross_provider_fallback,
            cfg.allow_cross_provider_fallback_on_quota,
        )
        if cache_key in self._candidate_cache:
            return list(self._candidate_cache[cache_key])

        chain: List[LLMModel] = [self._model]
        if not (cfg.fallback_enabled or cfg.fallback_on_quota_exceeded) or not self._llm_model_service:
            self._candidate_cache[cache_key] = chain
            return chain
        try:
            all_models = await self._llm_model_service.list_models(mask=False)
        except Exception:
            self._candidate_cache[cache_key] = chain
            return chain

        allow_cross = (
            (cfg.fallback_enabled and cfg.allow_cross_provider_fallback)
            or (cfg.fallback_on_quota_exceeded and cfg.allow_cross_provider_fallback_on_quota)
        )
        seen = {self._model.id}
        same_provider: List[LLMModel] = []

        for candidate in all_models:
            if candidate.id in seen:
                continue
            if candidate.provider != self._model.provider:
                continue
            if not self._is_valid_fallback_candidate(candidate, require_vision=require_vision):
                continue
            same_provider.append(candidate)
            seen.add(candidate.id)

        if self._thinking_enabled:
            same_provider.sort(
                key=lambda model: (0 if self._thinking_enabled_for(model, True) else 1),
            )
        chain.extend(same_provider)

        if allow_cross:
            for candidate in all_models:
                if candidate.id in seen:
                    continue
                if candidate.provider == self._model.provider:
                    continue
                if not self._is_valid_fallback_candidate(candidate, require_vision=require_vision):
                    continue
                chain.append(candidate)
                seen.add(candidate.id)

        self._candidate_cache[cache_key] = chain
        return chain

    @staticmethod
    def _is_valid_fallback_candidate(candidate: LLMModel, *, require_vision: bool) -> bool:
        caps = candidate.capabilities
        if require_vision and not (caps.vision or candidate.supports_multimodal):
            return False
        if candidate.provider != LLMProvider.OLLAMA and not candidate.api_key.strip():
            return False
        return True

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
        resilience_config: Optional[ModelResilienceConfig] = None,
) -> ResilientLLMClient:
    inner = LLMFactory.create(
        model,
        thinking_enabled=ResilientLLMClient._thinking_enabled_for(model, thinking_enabled),
    )
    return ResilientLLMClient(
        inner,
        model,
        llm_model_service=llm_model_service,
        thinking_enabled=thinking_enabled,
        resilience_config=resilience_config,
    )
