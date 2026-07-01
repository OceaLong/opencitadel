#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json

from app.domain.models.llm_model import LLMModel, LLMProvider
from app.domain.schemas.planner_output import PlannerPlanSchema
from app.infrastructure.external.llm.anthropic_llm import AnthropicLLM
from app.infrastructure.external.llm.structured_output import to_openai_strict, to_gemini_schema


def test_openai_strict_schema_inlines_array_refs_and_preserves_enum():
    payload = to_openai_strict(PlannerPlanSchema)
    schema = payload["json_schema"]["schema"]
    assert payload["json_schema"]["strict"] is True
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) >= {"title", "goal", "language", "steps", "status"}
    step_schema = schema["properties"]["steps"]["items"]
    assert step_schema["additionalProperties"] is False
    assert "description" in step_schema["required"]
    assert "enum" in step_schema["properties"]["status"]


def test_gemini_schema_removes_additional_properties():
    schema = to_gemini_schema(PlannerPlanSchema)
    assert "additionalProperties" not in json.dumps(schema)
    assert schema["properties"]["steps"]["items"]["properties"]["description"]["type"] == "string"


class _FakeAnthropicStreamResponse:
    status_code = 200

    async def aiter_lines(self):
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "emit_result"},
            },
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"title"'}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": ':"ok"}'}},
            {"type": "message_delta", "usage": {"output_tokens": 2}},
        ]
        for event in events:
            yield "data: " + json.dumps(event)


async def _collect_anthropic_synthetic_stream():
    llm = AnthropicLLM(LLMModel(provider=LLMProvider.ANTHROPIC, api_key="sk-test", model_name="claude-test"))
    return [
        chunk
        async for chunk in llm._iter_stream_lines(_FakeAnthropicStreamResponse(), synthetic_schema=True)
    ]


def test_anthropic_synthetic_tool_stream_yields_content_not_tool_calls():
    chunks = asyncio.run(_collect_anthropic_synthetic_stream())
    assert chunks[0] == {"content": '{"title"'}
    assert chunks[1] == {"content": ':"ok"}'}
    assert all("tool_calls" not in chunk for chunk in chunks)
    assert chunks[-1]["usage"]["total_tokens"] == 3

