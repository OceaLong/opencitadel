#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Global monotonic event seq allocator (unifies SSE cursor with DB session_events.seq)."""
import asyncio
import logging

from sqlalchemy import func, select, text

from app.infrastructure.models import SessionEventModel
from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.storage.redis import get_redis

logger = logging.getLogger(__name__)

GLOBAL_EVENT_SEQ_KEY = "session_events:global_seq"
_BLOCK_SIZE = 64

_local_next: int | None = None
_local_end: int | None = None
_alloc_lock = asyncio.Lock()


async def get_global_max_event_seq() -> int:
    async with get_postgres().session_factory() as session:
        result = await session.execute(select(func.max(SessionEventModel.seq)))
        value = result.scalar_one_or_none()
        return int(value or 0)


async def _sync_postgres_seq_counter(value: int) -> None:
    if value <= 0:
        return
    async with get_postgres().session_factory() as session:
        await session.execute(
            text("SELECT setval('session_events_seq_seq', :seq, true)"),
            {"seq": value},
        )
        await session.commit()


async def sync_global_event_seq() -> None:
    max_seq = await get_global_max_event_seq()
    redis = get_redis()
    current = await redis.client.get(GLOBAL_EVENT_SEQ_KEY)
    sync_value = max_seq
    if current is not None:
        sync_value = max(max_seq, int(current))
    await redis.client.set(GLOBAL_EVENT_SEQ_KEY, sync_value)
    await _sync_postgres_seq_counter(sync_value)
    global _local_next, _local_end
    async with _alloc_lock:
        _local_next = None
        _local_end = None
    logger.info("Synced global event seq counter to %s", sync_value)


async def allocate_event_seq() -> int:
    global _local_next, _local_end
    async with _alloc_lock:
        if _local_next is None or _local_next > _local_end:
            redis = get_redis()
            new_end = int(await redis.client.incrby(GLOBAL_EVENT_SEQ_KEY, _BLOCK_SIZE))
            _local_end = new_end
            _local_next = new_end - _BLOCK_SIZE + 1
        seq = _local_next
        _local_next += 1
        return seq
