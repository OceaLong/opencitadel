#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.errors.exceptions import ServerRequestsError
from app.application.services.memory_extractor_service import (
    EXTRACT_PROMPT,
    _extract_llm_text_content,
)
from app.domain.models.error_codes import MODEL_QUOTA_EXCEEDED
from app.domain.utils.llm_retry import (
    classify_llm_error_code,
    is_quota_exhausted_error,
    is_quota_fallback_eligible,
    is_retriable_llm_error,
)
from app.infrastructure.external.llm.openai_llm import _format_llm_error

QUOTA_ERROR_SAMPLE = (
    "Error code: 403 - {'error': {'message': 'The free quota has been exhausted. "
    "To continue accessing the model on a paid basis, please complete your payment "
    "information (or disable the \"use free tier only\" mode in the management console "
    "if already completed)', 'type': 'insufficient_quota', 'code': 'insufficient_quota'}}"
)


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


def test_is_quota_exhausted_error_detects_insufficient_quota():
    assert is_quota_exhausted_error(Exception(QUOTA_ERROR_SAMPLE)) is True


def test_classify_llm_error_code_quota_exceeded():
    assert classify_llm_error_code(Exception(QUOTA_ERROR_SAMPLE)) == MODEL_QUOTA_EXCEEDED


def test_classify_llm_error_code_invalid_request():
    err = Exception(
        'data: {"error":{"code":"invalid_parameter_error","message":"The content field is a required field."}}'
    )
    from app.domain.models.error_codes import MODEL_INVALID_REQUEST

    assert classify_llm_error_code(err) == MODEL_INVALID_REQUEST


def test_is_retriable_llm_error_quota_exhausted():
    assert is_retriable_llm_error(Exception(QUOTA_ERROR_SAMPLE)) is False


def test_format_llm_error_quota_exhausted():
    msg = _format_llm_error(Exception(QUOTA_ERROR_SAMPLE), "qwen-plus")
    assert msg == "调用LLM失败: 模型 qwen-plus API 配额已耗尽"
    assert "insufficient_quota" not in msg
    assert "free quota" not in msg


def test_is_quota_fallback_eligible_matches_exhausted():
    assert is_quota_fallback_eligible(Exception(QUOTA_ERROR_SAMPLE)) is True


def test_quota_fallback_eligible_is_not_retriable():
    err = Exception(QUOTA_ERROR_SAMPLE)
    assert is_quota_fallback_eligible(err) is True
    assert is_retriable_llm_error(err) is False


def test_is_quota_exhausted_error_detects_chinese_server_requests_error():
    err = ServerRequestsError("调用LLM失败: 模型 qwen-plus API 配额已耗尽")
    assert is_quota_exhausted_error(err) is True
    assert is_quota_fallback_eligible(err) is True
    assert classify_llm_error_code(err) == MODEL_QUOTA_EXCEEDED


def test_is_quota_exhausted_error_detects_cause_chain():
    original = Exception(QUOTA_ERROR_SAMPLE)
    wrapped = ServerRequestsError("调用LLM失败: 模型 qwen-plus API 配额已耗尽")
    wrapped.__cause__ = original
    assert is_quota_exhausted_error(wrapped) is True


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
