#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Pure LLM retry / breaker classification helpers (no infrastructure dependencies)."""
from typing import Iterator, Optional

from app.domain.models import error_codes as EC

_QUOTA_MARKERS = (
    "insufficient_quota",
    "quota has been exhausted",
    "free quota",
    "exceeded your current quota",
    "配额已耗尽",
    "配额耗尽",
)


def _iter_error_chain(error: BaseException, *, max_depth: int = 3) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: Optional[BaseException] = error
    depth = 0
    while current is not None and id(current) not in seen and depth < max_depth:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__
        depth += 1


def _text_matches_quota_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered or marker in text for marker in _QUOTA_MARKERS)


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
    for exc in _iter_error_chain(error):
        if _text_matches_quota_marker(str(exc)):
            return True
    return False


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
    from app.domain.services.agents.retry_budget import RetryBudgetExceeded

    if isinstance(error, RetryBudgetExceeded):
        return EC.TASK_INFRA_FAILED

    text = str(error).lower()
    if "重试预算" in str(error) or "structured_validation_retry" in text:
        return EC.TASK_INFRA_FAILED
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
