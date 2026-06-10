#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Pure LLM retry classification helpers (no infrastructure dependencies)."""


def is_retriable_llm_error(error: Exception) -> bool:
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
