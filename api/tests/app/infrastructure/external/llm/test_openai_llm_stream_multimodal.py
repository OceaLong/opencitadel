#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, MagicMock
from app.domain.models.llm_model import LLMModel, LLMProvider, ModelCapabilities
from app.infrastructure.external.llm.openai_llm import OpenAILLM


def test_stream_invoke_multimodal_fallback_on_connection_error():
    model = LLMModel(
        provider=LLMProvider.OPENAI,
        model_name="gpt-4o",
        api_key="sk-test",
        capabilities=ModelCapabilities(vision=True),
        supports_multimodal=True,
    )
    llm = OpenAILLM(model)

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "hi"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ],
    }]

    mock_stream = AsyncMock()
    mock_stream.__aiter__.return_value = iter([])

    create_mock = AsyncMock(
        side_effect=[ConnectionError("connection error"), mock_stream],
    )
    llm._client = MagicMock()
    llm._client.chat.completions.create = create_mock

    async def _run():
        chunks = []
        async for delta in llm.stream_invoke(messages):
            chunks.append(delta)
        return chunks

    asyncio.run(_run())

    assert create_mock.call_count == 2
    second_call_kwargs = create_mock.call_args_list[1].kwargs
    fallback_messages = second_call_kwargs.get("messages", [])
    content = fallback_messages[0]["content"]
    assert isinstance(content, str) or not any(
        p.get("type") == "image_url" for p in content if isinstance(content, list)
    )
