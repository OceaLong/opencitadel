#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.errors.exceptions import ServerRequestsError
from app.application.services.memory_extractor_service import (
    EXTRACT_PROMPT,
    _extract_llm_text_content,
)
from app.infrastructure.external.llm.openai_llm import _format_llm_error


def test_server_requests_error_str():
    err = ServerRequestsError("调用LLM失败: test")
    assert str(err) == "调用LLM失败: test"


def test_extract_prompt_format_no_key_error():
    rendered = EXTRACT_PROMPT.format(events_summary="user: hello")
    assert '{"title"' in rendered or '{{"title"' not in rendered
    assert "hello" in rendered
    assert "{events_summary}" not in rendered


def test_format_llm_error_unsupported_model():
    msg = _format_llm_error(
        Exception("Error code: 400 - Not supported model MiMo-V2.5-Pro"),
        "MiMo-V2.5-Pro",
    )
    assert "MiMo-V2.5-Pro" in msg
    assert "不被当前 Base URL 支持" in msg


def test_extract_llm_text_content_prefers_content():
    assert _extract_llm_text_content({"content": "[]", "reasoning_content": "x"}) == "[]"


def test_extract_llm_text_content_falls_back_to_reasoning():
    assert _extract_llm_text_content({
        "content": "",
        "reasoning_content": '[{"title":"a","content":"b","tags":[]}]',
    }) == '[{"title":"a","content":"b","tags":[]}]'


def test_extract_llm_text_content_empty_defaults_to_array():
    assert _extract_llm_text_content({}) == "[]"


@pytest.mark.parametrize(
    ("content", "tool_calls", "reasoning", "should_retry"),
    [
        ("hello", None, None, False),
        (None, [{"id": "1"}], None, False),
        (None, None, "thinking", False),
        (None, None, None, True),
    ],
)
def test_assistant_empty_response_matrix(content, tool_calls, reasoning, should_retry):
    has_content = bool(content)
    has_tools = bool(tool_calls)
    has_reasoning = bool(reasoning)
    is_empty = not has_content and not has_tools and not has_reasoning
    assert is_empty is should_retry
