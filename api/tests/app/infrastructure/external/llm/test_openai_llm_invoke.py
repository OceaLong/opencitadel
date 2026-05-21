#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.errors.exceptions import ServerRequestsError
from app.domain.models.llm_model import LLMModel, LLMProvider
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
