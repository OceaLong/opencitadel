#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Redact sensitive values from audit payloads."""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, List

_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "authorization", "credential", "access_token", "refresh_token",
})
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\b1[3-9]\d{9}\b|\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")
_REDACTED = "***REDACTED***"


def _mask_string(value: str) -> str:
    masked = _EMAIL_RE.sub("[email]", value)
    masked = _PHONE_RE.sub("[phone]", masked)
    return masked


def redact_value(key: str, value: Any) -> Any:
    key_lower = (key or "").lower()
    if any(part in key_lower for part in _SENSITIVE_KEYS):
        return _REDACTED
    if isinstance(value, str):
        return _mask_string(value)
    if isinstance(value, dict):
        return redact_payload(value)
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    return value


def redact_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (payload or {}).items():
        out[key] = redact_value(key, value)
    return out


def summarize_tool_result(result: Any, max_chars: int = 500) -> str:
    text = ""
    if result is None:
        return ""
    if hasattr(result, "message"):
        text = str(getattr(result, "message") or "")
    elif hasattr(result, "model_dump"):
        text = str(result.model_dump())
    else:
        text = str(result)
    text = _mask_string(text)
    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def redact_tool_args(args: Dict[str, Any]) -> Dict[str, Any]:
    return redact_payload(deepcopy(args or {}))
