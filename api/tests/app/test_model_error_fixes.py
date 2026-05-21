#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.application.errors.exceptions import ServerRequestsError
from app.application.services.memory_extractor_service import EXTRACT_PROMPT
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
