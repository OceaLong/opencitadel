#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.memory import Memory


def test_compact_preserves_assistant_reasoning_content():
    memory = Memory(messages=[
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc1", "function": {"name": "browser_view"}}],
            "reasoning_content": "thinking trace",
        },
        {
            "role": "tool",
            "tool_call_id": "tc1",
            "content": '{"data": {"content": "long page"}}',
            "_function_name": "browser_view",
        },
    ])
    memory.compact()
    assistant = memory.messages[0]
    tool = memory.messages[1]
    assert assistant.get("reasoning_content") == "thinking trace"
    assert tool["content"] == "(removed)"


def test_compact_removes_non_assistant_reasoning_content():
    memory = Memory(messages=[
        {
            "role": "tool",
            "tool_call_id": "tc1",
            "content": "ok",
            "reasoning_content": "should drop",
        },
    ])
    memory.compact()
    assert "reasoning_content" not in memory.messages[0]
