#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Redis pub/sub notifications for session list changes (replaces polling)."""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SESSION_LIST_CHANNEL = "sessions:list:notify"
_DEBOUNCE_SECONDS = 0.2

_debounce_task: Optional[asyncio.Task] = None
_debounce_lock = asyncio.Lock()


async def _publish_sessions_changed() -> None:
    try:
        from app.infrastructure.storage.redis import get_redis

        await get_redis().client.publish(SESSION_LIST_CHANNEL, "1")
    except Exception as exc:
        logger.debug("Session list notify failed: %s", exc)


async def _debounced_publish() -> None:
    try:
        await asyncio.sleep(_DEBOUNCE_SECONDS)
        await _publish_sessions_changed()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("Session list debounced publish failed: %s", exc)


async def notify_sessions_changed() -> None:
    """Publish a lightweight signal that the session list may have changed."""
    global _debounce_task
    try:
        async with _debounce_lock:
            if _debounce_task is not None and not _debounce_task.done():
                _debounce_task.cancel()
            _debounce_task = asyncio.create_task(_debounced_publish())
    except Exception as exc:
        logger.debug("Session list notify scheduling failed: %s", exc)
