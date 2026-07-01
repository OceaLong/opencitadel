#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.errors.exceptions import ServerRequestsError
from app.domain.models.llm_model import LLMModel, LLMProvider, ModelCapabilities
from app.domain.schemas.planner_output import PlannerPlanSchema
from app.infrastructure.external.llm.base_llm import (
    _has_multimodal_image_content,
    _strip_multimodal_to_text,
    normalize_usage,
)
from app.infrastructure.external.llm.openai_llm import OpenAILLM


async def _test_openai_llm_invoke_rejects_empty_choices():
    llm = OpenAILLM(
        LLMModel(
            provider=LLMProvider.OPENAI,
            base_url="https://example.com/v1",
            api_key="sk-test",
            model_name="deepseek-chat",
        )
    )
    response = SimpleNamespace(choices=[], usage=None)
    llm._client = MagicMock()
    llm._client.chat.completions.create = AsyncMock(return_value=response)

    with pytest.raises(ServerRequestsError, match="choices 为空"):
        await llm.invoke([{"role": "user", "content": "hi"}])


def test_openai_llm_invoke_rejects_empty_choices():
    asyncio.run(_test_openai_llm_invoke_rejects_empty_choices())


async def _test_openai_llm_invoke_merges_thinking_params():
    llm = OpenAILLM(
        LLMModel(
            provider=LLMProvider.OPENAI,
            base_url="https://example.com/v1",
            api_key="sk-test",
            model_name="deepseek-chat",
            extra_params={
                "thinking_model_name": "deepseek-reasoner",
                "thinking_request_params": {"enable_thinking": True},
            },
        ),
        thinking_enabled=True,
    )
    message = SimpleNamespace(
        model_dump=lambda: {"role": "assistant", "content": "ok"},
    )
    choice = SimpleNamespace(message=message, finish_reason="stop")
    response = SimpleNamespace(choices=[choice], usage=None)
    llm._client = MagicMock()
    llm._client.chat.completions.create = AsyncMock(return_value=response)

    result = await llm.invoke([{"role": "user", "content": "hi"}])
    assert result["content"] == "ok"
    kwargs = llm._client.chat.completions.create.await_args.kwargs
    assert kwargs["model"] == "deepseek-reasoner"
    assert kwargs["enable_thinking"] is True


def test_openai_llm_invoke_merges_thinking_params():
    asyncio.run(_test_openai_llm_invoke_merges_thinking_params())


async def _test_openai_llm_invoke_uses_configured_timeout():
    llm = OpenAILLM(
        LLMModel(
            provider=LLMProvider.OPENAI,
            base_url="https://example.com/v1",
            api_key="sk-test",
            model_name="deepseek-chat",
            extra_params={"request_timeout": 90},
        )
    )
    assert llm._timeout == 90

    message = SimpleNamespace(
        model_dump=lambda: {"role": "assistant", "content": "ok"},
    )
    choice = SimpleNamespace(message=message, finish_reason="stop")
    response = SimpleNamespace(choices=[choice], usage=None)
    llm._client = MagicMock()
    llm._client.chat.completions.create = AsyncMock(return_value=response)

    await llm.invoke([{"role": "user", "content": "hi"}])
    kwargs = llm._client.chat.completions.create.await_args.kwargs
    assert kwargs["timeout"] == 90


def test_openai_llm_invoke_uses_configured_timeout():
    asyncio.run(_test_openai_llm_invoke_uses_configured_timeout())


async def _test_openai_llm_invoke_timeout_raises_server_requests_error():
    llm = OpenAILLM(
        LLMModel(
            provider=LLMProvider.OPENAI,
            base_url="https://example.com/v1",
            api_key="sk-test",
            model_name="deepseek-chat",
            extra_params={"request_timeout": 30},
        )
    )
    llm._client = MagicMock()
    llm._client.chat.completions.create = AsyncMock(side_effect=TimeoutError("request timed out"))

    with pytest.raises(ServerRequestsError, match="调用LLM超时"):
        await llm.invoke([{"role": "user", "content": "hi"}])


def test_openai_llm_invoke_timeout_raises_server_requests_error():
    asyncio.run(_test_openai_llm_invoke_timeout_raises_server_requests_error())


def test_has_multimodal_image_content():
    assert _has_multimodal_image_content([
        {"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}]},
    ]) is True
    assert _has_multimodal_image_content([{"role": "user", "content": "hi"}]) is False


def test_strip_multimodal_to_text_keeps_text_and_removes_images():
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]},
    ]
    fallback = _strip_multimodal_to_text(messages)
    assert fallback[0]["content"] == "describe this"


def test_strip_multimodal_to_text_adds_note_for_image_only_message():
    messages = [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]},
    ]
    fallback = _strip_multimodal_to_text(messages)
    assert "图片附件" in fallback[0]["content"]


async def _test_openai_llm_invoke_multimodal_connection_error_falls_back_to_text():
    llm = OpenAILLM(
        LLMModel(
            provider=LLMProvider.OPENAI,
            base_url="https://example.com/v1",
            api_key="sk-test",
            model_name="gpt-4o",
            supports_multimodal=True,
        )
    )
    message = SimpleNamespace(
        model_dump=lambda: {"role": "assistant", "content": "ok"},
    )
    choice = SimpleNamespace(message=message, finish_reason="stop")
    response = SimpleNamespace(choices=[choice], usage=None)
    llm._client = MagicMock()
    llm._client.chat.completions.create = AsyncMock(
        side_effect=[
            Exception("Connection error."),
            response,
        ]
    )

    multimodal_messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ],
    }]
    result = await llm.invoke(multimodal_messages)
    assert result["content"] == "ok"
    assert llm._client.chat.completions.create.await_count == 2
    second_kwargs = llm._client.chat.completions.create.await_args_list[1].kwargs
    assert second_kwargs["messages"][0]["content"] == "describe this"
    assert not _has_multimodal_image_content(second_kwargs["messages"])


def test_openai_llm_invoke_multimodal_connection_error_falls_back_to_text():
    asyncio.run(_test_openai_llm_invoke_multimodal_connection_error_falls_back_to_text())


async def _test_openai_llm_invoke_multimodal_fallback_preserves_tools():
    llm = OpenAILLM(
        LLMModel(
            provider=LLMProvider.OPENAI,
            base_url="https://example.com/v1",
            api_key="sk-test",
            model_name="gpt-4o",
            supports_multimodal=True,
        )
    )
    message = SimpleNamespace(
        model_dump=lambda: {"role": "assistant", "content": "ok", "tool_calls": []},
    )
    choice = SimpleNamespace(message=message, finish_reason="stop")
    response = SimpleNamespace(choices=[choice], usage=None)
    llm._client = MagicMock()
    llm._client.chat.completions.create = AsyncMock(
        side_effect=[
            Exception("Connection error."),
            response,
        ]
    )
    tools = [{"type": "function", "function": {"name": "search", "parameters": {}}}]

    await llm.invoke(
        [{
            "role": "user",
            "content": [
                {"type": "text", "text": "find this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        }],
        tools=tools,
    )
    second_kwargs = llm._client.chat.completions.create.await_args_list[1].kwargs
    assert second_kwargs["tools"] == tools
    assert second_kwargs["parallel_tool_calls"] is True


def test_openai_llm_invoke_multimodal_fallback_preserves_tools():
    asyncio.run(_test_openai_llm_invoke_multimodal_fallback_preserves_tools())


async def _test_openai_llm_invoke_text_only_connection_error_no_fallback():
    llm = OpenAILLM(
        LLMModel(
            provider=LLMProvider.OPENAI,
            base_url="https://example.com/v1",
            api_key="sk-test",
            model_name="deepseek-chat",
        )
    )
    llm._client = MagicMock()
    llm._client.chat.completions.create = AsyncMock(side_effect=Exception("Connection error."))

    with pytest.raises(ServerRequestsError, match="Connection error"):
        await llm.invoke([{"role": "user", "content": "hi"}])
    assert llm._client.chat.completions.create.await_count == 1


def test_openai_llm_invoke_text_only_connection_error_no_fallback():
    asyncio.run(_test_openai_llm_invoke_text_only_connection_error_no_fallback())


async def _test_openai_llm_invoke_multimodal_invalid_image_falls_back_to_text():
    llm = OpenAILLM(
        LLMModel(
            provider=LLMProvider.OPENAI,
            base_url="https://example.com/v1",
            api_key="sk-test",
            model_name="gpt-4o",
            capabilities=ModelCapabilities(vision=True),
        )
    )
    message = SimpleNamespace(
        model_dump=lambda: {"role": "assistant", "content": "ok"},
    )
    choice = SimpleNamespace(message=message, finish_reason="stop")
    response = SimpleNamespace(choices=[choice], usage=None)
    llm._client = MagicMock()

    class BadRequestError(Exception):
        status_code = 400

    llm._client.chat.completions.create = AsyncMock(
        side_effect=[BadRequestError("invalid image"), response]
    )

    result = await llm.invoke([{
        "role": "user",
        "content": [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ],
    }])
    assert result["content"] == "ok"
    assert llm._client.chat.completions.create.await_count == 2


def test_openai_llm_invoke_multimodal_invalid_image_falls_back_to_text():
    asyncio.run(_test_openai_llm_invoke_multimodal_invalid_image_falls_back_to_text())


def test_normalize_usage_extracts_provider_cache_fields():
    openai_usage = normalize_usage({
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 64},
    })
    assert openai_usage["cached_tokens"] == 64

    deepseek_usage = normalize_usage({
        "prompt_cache_hit_tokens": 33,
        "prompt_cache_miss_tokens": 67,
    })
    assert deepseek_usage["cached_tokens"] == 33
    assert deepseek_usage["cache_write_tokens"] == 67

    anthropic_usage = normalize_usage({
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_read_input_tokens": 40,
        "cache_creation_input_tokens": 60,
    })
    assert anthropic_usage["cached_tokens"] == 40
    assert anthropic_usage["cache_write_tokens"] == 60

    gemini_usage = normalize_usage({
        "promptTokenCount": 100,
        "candidatesTokenCount": 10,
        "cachedContentTokenCount": 50,
    })
    assert gemini_usage["cached_tokens"] == 50


async def _test_openai_compatible_schema_uses_json_object_response_format():
    llm = OpenAILLM(
        LLMModel(
            provider=LLMProvider.OPENAI,
            base_url="https://api.deepseek.com/v1",
            api_key="sk-test",
            model_name="deepseek-chat",
        )
    )
    message = SimpleNamespace(model_dump=lambda: {"role": "assistant", "content": "{}"})
    response = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)
    llm._client = MagicMock()
    llm._client.chat.completions.create = AsyncMock(return_value=response)

    await llm.invoke(
        [{"role": "user", "content": "plan"}],
        response_schema={"model_class": PlannerPlanSchema, "schema": {}, "name": "PlannerPlanSchema"},
    )

    kwargs = llm._client.chat.completions.create.await_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}


def test_openai_compatible_schema_uses_json_object_response_format():
    asyncio.run(_test_openai_compatible_schema_uses_json_object_response_format())
