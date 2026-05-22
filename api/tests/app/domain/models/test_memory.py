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


def test_compact_strips_old_image_parts():
    memory = Memory(messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,old"}},
            ],
        },
        {"role": "assistant", "content": "ok"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "latest"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,new"}},
            ],
        },
    ])
    memory.compact()
    first_content = memory.messages[0]["content"]
    latest_content = memory.messages[2]["content"]
    assert any(
        part.get("type") == "text" and "[image omitted in compact]" in part.get("text", "")
        for part in first_content
        if isinstance(first_content, list)
    )
    assert any(
        part.get("type") == "image_url"
        for part in latest_content
        if isinstance(latest_content, list)
    )
