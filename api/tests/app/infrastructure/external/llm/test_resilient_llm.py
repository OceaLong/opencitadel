#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.models.llm_model import LLMModel, LLMProvider, ModelCapabilities
from app.infrastructure.external.llm.resilient_llm import ModelUnavailableError, ResilientLLMClient


class _FakeLLM:
    def __init__(self, *, response=None, error: Exception | None = None) -> None:
        self.response = response or {"content": "ok"}
        self.error = error
        self.invoke_count = 0

    model_name = "fake"
    temperature = 0.7
    max_tokens = 1024
    supports_multimodal = False

    @property
    def capabilities(self):
        return ModelCapabilities()

    async def invoke(self, messages, tools=None, response_format=None, tool_choice=None):
        self.invoke_count += 1
        if self.error is not None:
            raise self.error
        return self.response

    async def stream_invoke(self, messages, tools=None, response_format=None, tool_choice=None):
        yield {"content": "hello"}
        raise RuntimeError("503 service unavailable")


def _model(model_id: str) -> LLMModel:
    return LLMModel(
        id=model_id,
        display_name=model_id,
        model_name=f"gpt-{model_id}",
        provider=LLMProvider.OPENAI,
        base_url="http://localhost",
        api_key="sk-test",
    )


def _runtime_config(*, fallback_enabled: bool = True):
    return SimpleNamespace(
        model_resilience=SimpleNamespace(
            enabled=True,
            fallback_enabled=fallback_enabled,
            allow_cross_provider_fallback=False,
            max_attempts_per_call=1,
            max_call_budget_seconds=120.0,
            fast_fail_on_open_circuit=True,
        )
    )


async def _test_stream_invoke_no_midstream_fallback_after_delta():
    model = _model("m1")
    client = ResilientLLMClient(_FakeLLM(), model)
    chunks = []
    with pytest.raises(Exception):
        async for chunk in client.stream_invoke([{"role": "user", "content": "hi"}]):
            chunks.append(chunk)
    assert chunks == [{"content": "hello"}]
    assert client.streaming_started is True


def test_stream_invoke_no_midstream_fallback_after_delta():
    asyncio.run(_test_stream_invoke_no_midstream_fallback_after_delta())


async def _test_open_primary_falls_back_to_allowed_candidate():
    primary = _model("m1")
    fallback = _model("m2")
    fallback_llm = _FakeLLM(response={"content": "fallback"})
    llm_model_service = MagicMock()
    llm_model_service.list_models = AsyncMock(return_value=[primary, fallback])
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(side_effect=["deny", "allow"])
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()

    client = ResilientLLMClient(
        _FakeLLM(response={"content": "primary"}),
        primary,
        llm_model_service=llm_model_service,
    )
    client._breaker = breaker

    with patch("app.infrastructure.external.llm.resilient_llm.get_runtime_config", return_value=_runtime_config()), patch(
        "app.infrastructure.external.llm.resilient_llm.LLMFactory.create",
        return_value=fallback_llm,
    ):
        result = await client.invoke([{"role": "user", "content": "hi"}])

    assert result == {"content": "fallback"}
    assert fallback_llm.invoke_count == 1
    breaker.record_success.assert_awaited_once_with("m2")


def test_open_primary_falls_back_to_allowed_candidate():
    asyncio.run(_test_open_primary_falls_back_to_allowed_candidate())


async def _test_open_primary_without_fallback_fast_fails():
    primary = _model("m1")
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(return_value="deny")

    client = ResilientLLMClient(_FakeLLM(), primary)
    client._breaker = breaker

    with patch(
        "app.infrastructure.external.llm.resilient_llm.get_runtime_config",
        return_value=_runtime_config(fallback_enabled=False),
    ):
        with pytest.raises(ModelUnavailableError):
            await client.invoke([{"role": "user", "content": "hi"}])


def test_open_primary_without_fallback_fast_fails():
    asyncio.run(_test_open_primary_without_fallback_fast_fails())


async def _test_non_retriable_request_error_does_not_fallback():
    primary = _model("m1")
    fallback = _model("m2")
    llm_model_service = MagicMock()
    llm_model_service.list_models = AsyncMock(return_value=[primary, fallback])
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(return_value="allow")
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()

    client = ResilientLLMClient(
        _FakeLLM(error=RuntimeError("400 bad request invalid model")),
        primary,
        llm_model_service=llm_model_service,
    )
    client._breaker = breaker

    with patch("app.infrastructure.external.llm.resilient_llm.get_runtime_config", return_value=_runtime_config()), patch(
        "app.infrastructure.external.llm.resilient_llm.LLMFactory.create",
    ) as create:
        with pytest.raises(ModelUnavailableError):
            await client.invoke([{"role": "user", "content": "hi"}])

    create.assert_not_called()


def test_non_retriable_request_error_does_not_fallback():
    asyncio.run(_test_non_retriable_request_error_does_not_fallback())


async def _test_candidate_chain_is_cached_per_vision_requirement():
    primary = _model("m1")
    fallback = _model("m2")
    llm_model_service = MagicMock()
    llm_model_service.list_models = AsyncMock(return_value=[primary, fallback])
    client = ResilientLLMClient(_FakeLLM(), primary, llm_model_service=llm_model_service)

    with patch("app.infrastructure.external.llm.resilient_llm.get_runtime_config", return_value=_runtime_config()):
        first = await client._build_candidate_chain(require_vision=False)
        second = await client._build_candidate_chain(require_vision=False)

    assert [m.id for m in first] == ["m1", "m2"]
    assert [m.id for m in second] == ["m1", "m2"]
    llm_model_service.list_models.assert_awaited_once_with(mask=False)


def test_candidate_chain_is_cached_per_vision_requirement():
    asyncio.run(_test_candidate_chain_is_cached_per_vision_requirement())
