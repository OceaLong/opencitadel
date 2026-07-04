#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.services.agents.base import BaseAgent


def test_normalize_message_for_llm_reasoning_fallback_to_content():
    normalized = BaseAgent._normalize_message_for_llm({
        "role": "assistant",
        "content": None,
        "reasoning_content": "thinking trace",
    })
    assert normalized["content"] == "thinking trace"
    assert normalized.get("reasoning_content") == "thinking trace"


def test_normalize_message_for_llm_null_content_becomes_empty_string():
    normalized = BaseAgent._normalize_message_for_llm({
        "role": "assistant",
        "content": None,
    })
    assert normalized["content"] == ""


def test_assistant_message_from_llm_response_reasoning_only():
    message = BaseAgent._assistant_message_from_llm_response(
        content="",
        reasoning_content="only reasoning",
        tool_calls=None,
        stream_id="stream-1",
    )
    assert message["content"] == "only reasoning"
    assert message.get("reasoning_content") is None


def test_assistant_message_from_llm_response_preserves_tool_calls():
    tool_calls = [{"id": "call-1", "type": "function", "function": {"name": "search", "arguments": "{}"}}]
    message = BaseAgent._assistant_message_from_llm_response(
        content="",
        reasoning_content="",
        tool_calls=tool_calls,
        stream_id="stream-1",
    )
    assert message["content"] == ""
    assert message["tool_calls"] == tool_calls
