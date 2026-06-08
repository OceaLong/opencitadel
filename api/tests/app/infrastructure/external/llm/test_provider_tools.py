#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
from typing import Any, Dict, List

import pytest

from app.infrastructure.external.llm.anthropic_llm import AnthropicLLM
from app.infrastructure.external.llm.gemini_llm import GeminiLLM
from app.domain.models.llm_model import LLMModel, LLMProvider


def _model() -> LLMModel:
    return LLMModel(
        display_name="test",
        provider=LLMProvider.OPENAI,
        base_url="https://example.com",
        api_key="sk-test",
        model_name="test-model",
    )


def test_anthropic_converts_assistant_tool_calls_to_tool_use_blocks():
    llm = AnthropicLLM(_model())
    _, converted = llm._convert_messages([
        {"role": "assistant", "content": "calling tool", "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"filepath": "/tmp/a.txt"}'},
        }]},
    ])
    assert converted[0]["role"] == "assistant"
    blocks = converted[0]["content"]
    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "tool_use"
    assert blocks[1]["name"] == "read_file"
    assert blocks[1]["input"]["filepath"] == "/tmp/a.txt"


def test_gemini_converts_tools_and_parses_function_call_response():
    llm = GeminiLLM(_model())
    tools = llm._convert_tools([{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "read",
            "parameters": {"type": "object", "properties": {"filepath": {"type": "string"}}},
        },
    }])
    assert tools[0]["name"] == "read_file"

    contents = llm._convert_messages([
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"filepath": "/tmp/a.txt"}'},
        }]},
    ])
    assert contents[0]["parts"][0]["functionCall"]["name"] == "read_file"
