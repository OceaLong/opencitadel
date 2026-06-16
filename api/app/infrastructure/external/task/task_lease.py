#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Distributed task execution lease to prevent duplicate runs."""
from __future__ import annotations

import logging
import socket
import uuid

logger = logging.getLogger(__name__)

_LEASE_PREFIX = "task:execution:lease:"
_worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


def _lease_key(task_id: str) -> str:
    return f"{_LEASE_PREFIX}{task_id}"


def get_worker_id() -> str:
    return _worker_id


async def try_acquire_task_lease(task_id: str, ttl_seconds: int) -> bool:
    """Acquire exclusive execution lease for task_id. Returns False if held elsewhere."""
    if not task_id:
        return False
    try:
        from app.infrastructure.storage.redis import get_redis

        redis = get_redis().client
        acquired = await redis.set(
            _lease_key(task_id),
            _worker_id,
            nx=True,
            ex=max(10, ttl_seconds),
        )
        if not acquired:
            from app.infrastructure.observability.admission_metrics import record_task_lease_conflict

            record_task_lease_conflict()
        return bool(acquired)
    except Exception as exc:
        logger.warning("task lease acquire failed task_id=%s: %s", task_id, exc)
        return False


async def renew_task_lease(task_id: str, ttl_seconds: int) -> bool:
    if not task_id:
        return False
    try:
        from app.infrastructure.storage.redis import get_redis

        redis = get_redis().client
        key = _lease_key(task_id)
        owner = await redis.get(key)
        if owner != _worker_id:
            return False
        await redis.expire(key, max(10, ttl_seconds))
        return True
    except Exception as exc:
        logger.debug("task lease renew failed task_id=%s: %s", task_id, exc)
        return False


async def get_task_lease_owner(task_id: str) -> str | None:
    if not task_id:
        return None
    try:
        from app.infrastructure.storage.redis import get_redis

        owner = await get_redis().client.get(_lease_key(task_id))
        if isinstance(owner, bytes):
            return owner.decode()
        return owner
    except Exception as exc:
        logger.debug("task lease owner lookup failed task_id=%s: %s", task_id, exc)
        return None


async def release_task_lease(task_id: str) -> None:
    if not task_id:
        return
    try:
        from app.infrastructure.storage.redis import get_redis

        redis = get_redis().client
        key = _lease_key(task_id)
        owner = await redis.get(key)
        if owner == _worker_id:
            await redis.delete(key)
    except Exception as exc:
        logger.debug("task lease release failed task_id=%s: %s", task_id, exc)
