"""Resilient LLM wrapper: single retry authority, breaker, guarded fallback."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from app.application.ports.inference import (
    CircuitBreakerPort,
    InferenceProviderCatalog,
    ModelClientFactoryPort,
)
from app.application.ports.observability import ModelMetricsPort
from app.application.services.inference_model_service import InferenceModelService
from app.domain.errors import ServerRequestsError
from app.domain.external.llm import LLM
from app.domain.models.error_codes import (
    MODEL_NOT_CONFIGURED,
    MODEL_QUOTA_EXCEEDED,
    MODEL_UNAVAILABLE,
)
from app.domain.models.inference import ResolvedInferenceModel
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import ModelResiliencePolicy
from app.domain.utils.llm_retry import (
    classify_llm_error_code,
    is_quota_exhausted_error,
    is_quota_fallback_eligible,
    is_retriable_llm_error,
)
from app.infrastructure.external.llm.base_llm import (
    _has_multimodal_image_content,
    _strip_multimodal_to_text,
    is_retriable_multimodal_error,
)

logger = logging.getLogger(__name__)

_THINKING_PARAM_KEYS = frozenset({"thinking_request_params", "thinking_extra_body"})


class ModelUnavailableError(ServerRequestsError):
    """Raised when circuit is open or no invokable model remains."""

    def __init__(
        self, msg: str = "模型服务暂不可用", *, error_code: str = MODEL_UNAVAILABLE
    ) -> None:
        super().__init__(msg)
        self.error_code = error_code


class ResilientLLMClient:
    """Wraps a concrete LLM with resilience policies."""

    def __init__(
        self,
        inner: LLM,
        model: ResolvedInferenceModel,
        *,
        policy: ModelResiliencePolicy,
        breaker: CircuitBreakerPort,
        provider_catalog: InferenceProviderCatalog,
        model_client_factory: ModelClientFactoryPort,
        metrics: ModelMetricsPort,
        inference_model_service: InferenceModelService | None = None,
        scope: OwnerScope | None = None,
        thinking_enabled: bool = False,
    ) -> None:
        self._inner = inner
        self._model = model
        self._active_model = model
        self._inference_model_service = inference_model_service
        self._scope = scope
        self._thinking_enabled = thinking_enabled
        self._policy = policy
        self._breaker = breaker
        self._provider_catalog = provider_catalog
        self._model_client_factory = model_client_factory
        self._metrics = metrics
        self._streaming_started = False
        self._candidate_cache: dict[
            tuple[bool, bool, bool, bool, bool, bool],
            list[ResolvedInferenceModel],
        ] = {}
        self._quota_exhausted_model_ids: set[str] = set()
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
    def capabilities(self):
        return self._inner.capabilities

    @property
    def model_id(self) -> str:
        return self._model.id

    @property
    def active_model(self) -> ResolvedInferenceModel:
        return self._active_model

    @property
    def streaming_started(self) -> bool:
        return self._streaming_started

    @staticmethod
    def _thinking_enabled_for(
        model: ResolvedInferenceModel,
        session_thinking: bool,
    ) -> bool:
        if not session_thinking:
            return False
        extra = model.extra_params or {}
        return any(key in extra for key in _THINKING_PARAM_KEYS)

    def _mark_quota_exhausted(self, model_id: str) -> None:
        self._quota_exhausted_model_ids.add(model_id)

    def _should_skip_quota_exhausted(self, candidate: ResolvedInferenceModel) -> bool:
        return candidate.id in self._quota_exhausted_model_ids

    def _all_candidates_quota_exhausted(
        self,
        candidates: list[ResolvedInferenceModel],
    ) -> bool:
        if not self._quota_exhausted_model_ids:
            return False
        return all(candidate.id in self._quota_exhausted_model_ids for candidate in candidates)

    def _build_final_error(
        self,
        last_error: Exception | None,
        candidates: list[ResolvedInferenceModel],
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

    async def invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cfg = self._policy
        deadline = time.monotonic() + cfg.max_call_budget_seconds
        last_error: Exception | None = None
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
            candidate_error: Exception | None = None
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
                    )
                    await self._breaker.record_success(candidate.id, cfg)
                    self._active_model = candidate
                    if candidate.id != self._model.id:
                        self._metrics.record_resilience_event(
                            "fallback_success", candidate.id, candidate.provider.value
                        )
                    return result
                except (OSError, RuntimeError, ValueError) as exc:
                    last_error = exc
                    candidate_error = exc
                    await self._breaker.record_failure(candidate.id, exc, cfg)
                    self._metrics.record_resilience_event(
                        "invoke_error", candidate.id, candidate.provider.value
                    )
                    if cfg.fallback_on_quota_exceeded and is_quota_fallback_eligible(exc):
                        self._mark_quota_exhausted(candidate.id)
                        break
                    if is_retriable_llm_error(exc):
                        if attempts >= cfg.max_attempts_per_call:
                            break
                        delay = min(2 ** (attempts - 1), 8)
                        await asyncio.sleep(delay)
                        continue
                    raise ModelUnavailableError(
                        str(exc), error_code=classify_llm_error_code(exc)
                    ) from exc
            if not self._can_advance_to_next_candidate(
                cfg, candidate_error or last_error, candidate_idx, candidates
            ):
                break
        raise self._build_final_error(last_error, candidates, streaming=False)

    async def stream_invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        self._streaming_started = False
        cfg = self._policy
        deadline = time.monotonic() + cfg.max_call_budget_seconds
        last_error: Exception | None = None
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
            candidate_error: Exception | None = None
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
                    ):
                        self._streaming_started = True
                        self._active_model = candidate
                        yield chunk
                    await self._breaker.record_success(candidate.id, cfg)
                    if candidate.id != self._model.id:
                        self._metrics.record_resilience_event(
                            "fallback_success", candidate.id, candidate.provider.value
                        )
                    return
                except (OSError, RuntimeError, ValueError) as exc:
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
                    await self._breaker.record_failure(candidate.id, exc, cfg)
                    if cfg.fallback_on_quota_exceeded and is_quota_fallback_eligible(exc):
                        self._mark_quota_exhausted(candidate.id)
                        break
                    if is_retriable_llm_error(exc):
                        if attempts >= cfg.max_attempts_per_call:
                            break
                        delay = min(2 ** (attempts - 1), 8)
                        await asyncio.sleep(delay)
                        continue
                    raise ModelUnavailableError(
                        str(exc), error_code=classify_llm_error_code(exc)
                    ) from exc
            if self._streaming_started:
                break
            if not self._can_advance_to_next_candidate(
                cfg, candidate_error or last_error, candidate_idx, candidates
            ):
                break
        raise self._build_final_error(last_error, candidates, streaming=True)

    def _can_advance_to_next_candidate(
        self,
        cfg,
        error: Exception | None,
        candidate_idx: int,
        candidates: list[ResolvedInferenceModel],
    ) -> bool:
        if error is None or candidate_idx + 1 >= len(candidates):
            return False
        if is_quota_fallback_eligible(error) and cfg.fallback_on_quota_exceeded:
            return True
        if is_retriable_llm_error(error) and cfg.fallback_enabled:
            return True
        return bool(is_retriable_llm_error(error) and cfg.fallback_on_quota_exceeded)

    def _client_for(self, model: ResolvedInferenceModel) -> LLM:
        if model.id == self._model.id:
            return self._inner
        cached = self._fallback_clients.get(model.id)
        if cached is not None:
            return cached
        client = self._model_client_factory.create_model_client(
            model,
            thinking_enabled=self._thinking_enabled_for(model, self._thinking_enabled),
        )
        self._fallback_clients[model.id] = client
        return client

    async def _build_candidate_chain(
        self,
        *,
        require_vision: bool,
    ) -> list[ResolvedInferenceModel]:
        cfg = self._policy
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

        chain: list[ResolvedInferenceModel] = [self._model]
        if (
            not (cfg.fallback_enabled or cfg.fallback_on_quota_exceeded)
            or not self._inference_model_service
        ):
            self._candidate_cache[cache_key] = chain
            return chain
        try:
            all_models = await self._inference_model_service.list_resolved_chat_models(
                scope=self._scope
            )
        except (OSError, RuntimeError, ValueError):
            self._candidate_cache[cache_key] = chain
            return chain

        allow_cross = (cfg.fallback_enabled and cfg.allow_cross_provider_fallback) or (
            cfg.fallback_on_quota_exceeded and cfg.allow_cross_provider_fallback_on_quota
        )
        seen = {self._model.id}
        same_provider: list[ResolvedInferenceModel] = []

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
                key=lambda model: 0 if self._thinking_enabled_for(model, True) else 1,
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

    def _is_valid_fallback_candidate(
        self,
        candidate: ResolvedInferenceModel,
        *,
        require_vision: bool,
    ) -> bool:
        caps = candidate.capabilities
        if require_vision and not caps.vision:
            return False
        return not (
            self._provider_catalog.credential_required(candidate.provider)
            and not candidate.credential.strip()
        )

    async def _candidate_allowed(self, candidate: ResolvedInferenceModel) -> bool:
        cfg = self._policy
        if not cfg.enabled or not cfg.fast_fail_on_open_circuit:
            return True
        decision = await self._breaker.allow_request(candidate.id, cfg)
        if decision == "deny":
            self._metrics.record_resilience_event(
                "circuit_open_fast_fail",
                candidate.id,
                candidate.provider.value,
            )
            return False
        return True

    @staticmethod
    def _needs_vision(messages: list[dict[str, Any]]) -> bool:
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        return True
        return False


def create_resilient_llm(
    model: ResolvedInferenceModel,
    *,
    policy: ModelResiliencePolicy,
    thinking_enabled: bool,
    inference_model_service: InferenceModelService,
    scope: OwnerScope,
    breaker: CircuitBreakerPort,
    provider_catalog: InferenceProviderCatalog,
    model_client_factory: ModelClientFactoryPort,
    metrics: ModelMetricsPort,
) -> ResilientLLMClient:
    inner = model_client_factory.create_model_client(
        model,
        thinking_enabled=ResilientLLMClient._thinking_enabled_for(model, thinking_enabled),
    )
    return ResilientLLMClient(
        inner,
        model,
        policy=policy,
        breaker=breaker,
        provider_catalog=provider_catalog,
        model_client_factory=model_client_factory,
        metrics=metrics,
        inference_model_service=inference_model_service,
        scope=scope,
        thinking_enabled=thinking_enabled,
    )
