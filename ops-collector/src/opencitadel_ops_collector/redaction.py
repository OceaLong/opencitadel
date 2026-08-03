"""Fail-closed secret-shaped text redaction."""
from __future__ import annotations

import re
from typing import Any


_PATTERNS = (
    re.compile(r"(?i)\b(authorization\s*:\s*bearer|bearer)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(password|passwd|pwd|api[_-]?key|token|secret)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\b(postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?)://[^\s]+"),
    re.compile(r"(?i)\b(cookie|set-cookie)\s*:\s*[^\r\n]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{8,})?\b"),
)


def redact_text(value: str) -> str:
    result = value
    for pattern in _PATTERNS:
        result = pattern.sub("***REDACTED***", result)
    return result


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: ("***REDACTED***" if any(token in key.lower() for token in ("password", "secret", "token", "authorization", "cookie", "dsn")) else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
