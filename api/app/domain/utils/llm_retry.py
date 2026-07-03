#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Pure LLM retry / breaker classification helpers (no infrastructure dependencies)."""
from typing import Optional

from app.domain.models import error_codes as EC


def is_retriable_llm_error(error: Exception) -> bool:
    """Transient infra errors eligible for retry (not breaker-only)."""
    text = str(error).lower()
    markers = (
        "429",
        "rate limit",
        "ratelimit",
        "503",
        "502",
        "500",
        "504",
        "timeout",
        "timed out",
        "overloaded",
        "temporarily unavailable",
        "connection reset",
        "connection error",
    )
    return any(marker in text for marker in markers)


def is_quota_exhausted_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(
        marker in text
        for marker in (
            "insufficient_quota",
            "quota has been exhausted",
            "free quota",
            "exceeded your current quota",
        )
    )


def is_quota_fallback_eligible(error: Exception) -> bool:
    return is_quota_exhausted_error(error)


def is_breaker_eligible_error(error: Exception) -> bool:
    """Only infra-level signals count toward circuit breaker windows."""
    text = str(error).lower()
    if not is_retriable_llm_error(error):
        return False
    excluded = (
        "400",
        "422",
        "bad request",
        "validation",
        "context_length",
        "context length",
        "maximum context",
        "not supported",
        "unsupported model",
        "invalid model",
        "content policy",
        "content_filter",
    )
    return not any(marker in text for marker in excluded)


def classify_llm_error_code(error: Exception) -> str:
    """Map an exception to a graded error code for ErrorEvent / DLQ."""
    text = str(error).lower()
    if "not configured" in text or "未配置" in text:
        return EC.MODEL_NOT_CONFIGURED
    if is_quota_exhausted_error(error):
        return EC.MODEL_QUOTA_EXCEEDED
    if "429" in text or "rate limit" in text or "ratelimit" in text:
        return EC.MODEL_RATE_LIMITED
    if "timeout" in text or "timed out" in text:
        return EC.MODEL_TIMEOUT
    if "model unavailable" in text or "熔断" in text or "circuit" in text:
        return EC.MODEL_UNAVAILABLE
    if is_retriable_llm_error(error):
        return EC.MODEL_UNAVAILABLE
    return EC.MODEL_UNAVAILABLE


def error_code_from_optional(code: Optional[str]) -> Optional[str]:
    if code and code in EC.ALL_ERROR_CODES:
        return code
    return None
