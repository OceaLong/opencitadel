#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.utils.audit_redaction import redact_payload, redact_tool_args, summarize_tool_result
from app.domain.models.tool_result import ToolResult


def test_redact_password_field():
    payload = {"username": "agent", "password": "secret123"}
    redacted = redact_payload(payload)
    assert redacted["password"] == "***REDACTED***"
    assert redacted["username"] == "agent"


def test_redact_email_in_string():
    payload = {"note": "contact user@example.com please"}
    redacted = redact_payload(payload)
    assert "[email]" in redacted["note"]


def test_redact_tool_args_copy():
    args = {"selector": "#btn-confirm-close", "password": "x"}
    out = redact_tool_args(args)
    assert args["password"] == "x"
    assert out["password"] == "***REDACTED***"


def test_summarize_tool_result_truncates():
    result = ToolResult(success=True, message="ok" * 200)
    summary = summarize_tool_result(result, max_chars=50)
    assert len(summary) <= 50
