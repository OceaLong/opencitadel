#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Global monotonic event seq allocator (unifies SSE cursor with DB session_events.seq)."""
import asyncio
import logging

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.infrastructure.models import SessionEventModel
from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.storage.redis import get_redis
from core.config import get_settings

logger = logging.getLogger(__name__)

GLOBAL_EVENT_SEQ_KEY = "session_events:global_seq"
# Must remain 1. Block prefetch (e.g. 64) breaks global monotonic ordering when API and
# Worker processes each cache a local block: user messages (API) and agent events
# (Worker) can get seq values that invert causal order on session_events replay.
_BLOCK_SIZE = 1

_alloc_lock = asyncio.Lock()


def _is_missing_session_events_schema(exc: BaseException) -> bool:
    message = str(getattr(exc, "orig", exc)).lower()
    return (
        "session_events" in message
        or "session_events_seq_seq" in message
        or "undefinedtableerror" in message
    )


async def get_global_max_event_seq() -> int:
    try:
        async with get_postgres().session_factory() as session:
            result = await session.execute(select(func.max(SessionEventModel.seq)))
            value = result.scalar_one_or_none()
            return int(value or 0)
    except (ProgrammingError, DBAPIError) as exc:
        if get_settings().env == "test" and _is_missing_session_events_schema(exc):
            logger.warning(
                "session_events schema missing in test env; assuming max seq=0: %s",
                exc,
            )
            return 0
        raise


async def _sync_postgres_seq_counter(value: int) -> None:
    if value <= 0:
        return
    try:
        async with get_postgres().session_factory() as session:
            await session.execute(
                text("SELECT setval('session_events_seq_seq', :seq, true)"),
                {"seq": value},
            )
            await session.commit()
    except (ProgrammingError, DBAPIError) as exc:
        if get_settings().env == "test" and _is_missing_session_events_schema(exc):
            logger.warning(
                "session_events sequence missing in test env; skipping setval: %s",
                exc,
            )
            return
        raise


async def sync_global_event_seq() -> None:
    redis = get_redis()
    current = await redis.client.get(GLOBAL_EVENT_SEQ_KEY)
    if current is not None and int(current) > 0:
        sync_value = int(current)
        await _sync_postgres_seq_counter(sync_value)
        logger.info("Restored global event seq counter from Redis: %s", sync_value)
        return

    max_seq = await get_global_max_event_seq()
    sync_value = max_seq
    await redis.client.set(GLOBAL_EVENT_SEQ_KEY, sync_value)
    await _sync_postgres_seq_counter(sync_value)
    logger.info("Seeded global event seq counter from database: %s", sync_value)


async def allocate_event_seq() -> int:
    """Allocate the next global event seq via a single atomic Redis INCRBY."""
    async with _alloc_lock:
        redis = get_redis()
        return int(await redis.client.incrby(GLOBAL_EVENT_SEQ_KEY, _BLOCK_SIZE))
