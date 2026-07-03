#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json

import pytest

from app.domain.models.memory import Memory, _extract_url_from_tool_content


def test_extract_url_from_tool_content_json_string():
    payload = json.dumps({"success": True, "data": {"url": "https://example.com", "content": "x"}})
    assert _extract_url_from_tool_content(payload) == "https://example.com"


def test_compact_preserves_browser_url():
    payload = json.dumps({"success": True, "data": {"url": "https://example.com", "content": "long page"}})
    memory = Memory(messages=[
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc1", "function": {"name": "browser_navigate"}}],
        },
        {
            "role": "tool",
            "tool_call_id": "tc1",
            "content": payload,
            "_function_name": "browser_navigate",
        },
    ])
    memory.compact()
    tool = memory.messages[1]
    assert "https://example.com" in tool["content"]
    assert tool["content"] != "(removed)"


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


def test_compact_truncation_includes_read_file_path():
    memory = Memory(messages=[
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "tc1",
                "function": {"name": "read_file", "arguments": '{"filepath": "/home/ubuntu/report.csv"}'},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "tc1",
            "content": "x" * 5000,
            "_function_name": "read_file",
        },
    ])
    memory.compact(tool_content_max_chars=2000)
    assert "/home/ubuntu/report.csv" in memory.messages[1]["content"]
    assert "[结果已截断" in memory.messages[1]["content"]


def test_compact_does_not_truncate_write_file_results():
    memory = Memory(messages=[
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "tc1",
                "function": {"name": "write_file", "arguments": '{"filepath": "/home/ubuntu/report.md"}'},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "tc1",
            "content": '{"success": true, "data": {"filepath": "/home/ubuntu/report.md", "bytes_written": 99999}}',
            "_function_name": "write_file",
        },
    ])
    memory.compact(tool_content_max_chars=2000)
    assert "[truncated]" not in memory.messages[1]["content"]
    assert "[结果已截断" not in memory.messages[1]["content"]


def test_compact_truncation_includes_search_web_query():
    memory = Memory(messages=[
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "tc2",
                "function": {"name": "search_web", "arguments": '{"query": "python asyncio tutorial"}'},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "tc2",
            "content": "y" * 5000,
            "_function_name": "search_web",
        },
    ])
    memory.compact(tool_content_max_chars=2000)
    assert "python asyncio tutorial" in memory.messages[1]["content"]
    assert "[结果已截断" in memory.messages[1]["content"]
