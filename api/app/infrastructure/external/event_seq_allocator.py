#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Global monotonic event seq allocator (unifies SSE cursor with DB session_events.seq)."""
import logging

from sqlalchemy import func, select

from app.infrastructure.models import SessionEventModel
from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.storage.redis import get_redis

logger = logging.getLogger(__name__)

GLOBAL_EVENT_SEQ_KEY = "session_events:global_seq"


async def get_global_max_event_seq() -> int:
    async with get_postgres().session_factory() as session:
        result = await session.execute(select(func.max(SessionEventModel.seq)))
        value = result.scalar_one_or_none()
        return int(value or 0)


async def sync_global_event_seq() -> None:
    max_seq = await get_global_max_event_seq()
    redis = get_redis()
    current = await redis.client.get(GLOBAL_EVENT_SEQ_KEY)
    if current is None or int(current) < max_seq:
        await redis.client.set(GLOBAL_EVENT_SEQ_KEY, max_seq)
        logger.info("Synced global event seq counter to %s", max_seq)


async def allocate_event_seq() -> int:
    redis = get_redis()
    return int(await redis.client.incr(GLOBAL_EVENT_SEQ_KEY))
