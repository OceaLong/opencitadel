#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared retry helpers for transient LLM API failures."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BASE_DELAY_SECONDS = 1.0


from app.domain.utils.llm_retry import is_retriable_llm_error  # noqa: F401 — re-export


async def with_llm_retry(
        operation: Callable[[], Awaitable[T]],
        *,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        base_delay_seconds: float = _DEFAULT_BASE_DELAY_SECONDS,
) -> T:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await operation()
        except Exception as error:
            last_error = error
            if attempt >= max_retries - 1 or not is_retriable_llm_error(error):
                raise
            delay = base_delay_seconds * (2 ** attempt)
            logger.warning(
                "LLM request failed (attempt %s/%s), retrying in %.1fs: %s",
                attempt + 1,
                max_retries,
                delay,
                error,
            )
            await asyncio.sleep(delay)
    if last_error is not None:
        raise last_error
    raise RuntimeError("LLM retry loop exited without result")
