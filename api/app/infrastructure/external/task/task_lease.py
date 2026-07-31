#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Distributed task lease for the supported non-cluster Redis runtime."""
from __future__ import annotations

import logging
import json
import socket
import uuid
from enum import Enum

logger = logging.getLogger(__name__)

_LEASE_PREFIX = "task:execution:lease:"
_worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


class TaskLeaseAcquireResult(str, Enum):
    ACQUIRED = "ACQUIRED"
    SAME_GENERATION_CONFLICT = "SAME_GENERATION_CONFLICT"
    STALE_GENERATION = "STALE_GENERATION"
    FUTURE_GENERATION = "FUTURE_GENERATION"
    TERMINAL = "TERMINAL"
    MISSING_TASK = "MISSING_TASK"
    FUTURE_LEASE = "FUTURE_LEASE"
    ERROR = "ERROR"


_ACQUIRE_GENERATION_LEASE = """
-- opencitadel:acquire-generation-lease
local raw_meta = redis.call("GET", KEYS[2])
if not raw_meta then
    return -1
end
local meta = cjson.decode(raw_meta)
local current_generation = tonumber(meta["run_generation"] or 1)
local requested_generation = tonumber(ARGV[1])
if requested_generation < current_generation then
    return -2
end
if requested_generation > current_generation then
    return -3
end
local status = meta["status"]
if status == "done" or status == "cancelled" or status == "failed" then
    return -4
end

local raw_lease = redis.call("GET", KEYS[1])
if raw_lease then
    local decoded, lease = pcall(cjson.decode, raw_lease)
    if not decoded or type(lease) ~= "table" then
        return -6
    end
    local lease_generation = tonumber(lease["run_generation"])
    if not lease_generation then
        return -6
    end
    if lease_generation == requested_generation then
        return 0
    end
    if lease_generation > requested_generation then
        return -5
    end
end
redis.call(
    "SET",
    KEYS[1],
    cjson.encode({worker_id=ARGV[2], run_generation=requested_generation}),
    "EX",
    ARGV[3]
)
return 1
"""

_RENEW_GENERATION_LEASE = """
-- opencitadel:renew-generation-lease
local raw_meta = redis.call("GET", KEYS[2])
local raw_lease = redis.call("GET", KEYS[1])
if not raw_meta or not raw_lease then
    return 0
end
local meta = cjson.decode(raw_meta)
local lease = cjson.decode(raw_lease)
if tonumber(meta["run_generation"] or 1) ~= tonumber(ARGV[1]) then
    return 0
end
if lease["worker_id"] ~= ARGV[2] then
    return 0
end
if tonumber(lease["run_generation"]) ~= tonumber(ARGV[1]) then
    return 0
end
redis.call("EXPIRE", KEYS[1], ARGV[3])
return 1
"""

_RELEASE_GENERATION_LEASE = """
-- opencitadel:release-generation-lease
local raw_lease = redis.call("GET", KEYS[1])
if not raw_lease then
    return 0
end
local lease = cjson.decode(raw_lease)
if lease["worker_id"] ~= ARGV[2] then
    return 0
end
if tonumber(lease["run_generation"]) ~= tonumber(ARGV[1]) then
    return 0
end
redis.call("DEL", KEYS[1])
return 1
"""


def _lease_key(task_id: str) -> str:
    return f"{_LEASE_PREFIX}{task_id}"


def _task_meta_key(task_id: str) -> str:
    return f"task:meta:{task_id}"


def get_worker_id() -> str:
    return _worker_id


async def try_acquire_task_lease(
        task_id: str,
        run_generation: int,
        ttl_seconds: int,
) -> TaskLeaseAcquireResult:
    """Atomically classify and, when safe, acquire the execution lease."""
    if not task_id:
        return TaskLeaseAcquireResult.ERROR
    try:
        from app.infrastructure.storage.redis import get_redis

        redis = get_redis().client
        result = int(await redis.eval(
            _ACQUIRE_GENERATION_LEASE,
            2,
            _lease_key(task_id),
            _task_meta_key(task_id),
            run_generation,
            _worker_id,
            max(10, ttl_seconds),
        ))
        classified = {
            1: TaskLeaseAcquireResult.ACQUIRED,
            0: TaskLeaseAcquireResult.SAME_GENERATION_CONFLICT,
            -1: TaskLeaseAcquireResult.MISSING_TASK,
            -2: TaskLeaseAcquireResult.STALE_GENERATION,
            -3: TaskLeaseAcquireResult.FUTURE_GENERATION,
            -4: TaskLeaseAcquireResult.TERMINAL,
            -5: TaskLeaseAcquireResult.FUTURE_LEASE,
            -6: TaskLeaseAcquireResult.ERROR,
        }.get(result, TaskLeaseAcquireResult.ERROR)
        if classified == TaskLeaseAcquireResult.SAME_GENERATION_CONFLICT:
            from app.infrastructure.observability.admission_metrics import record_task_lease_conflict

            record_task_lease_conflict()
        return classified
    except Exception as exc:
        logger.warning("task lease acquire failed task_id=%s: %s", task_id, exc)
        return TaskLeaseAcquireResult.ERROR


async def renew_task_lease(
        task_id: str,
        run_generation: int,
        ttl_seconds: int,
) -> bool:
    if not task_id:
        return False
    try:
        from app.infrastructure.storage.redis import get_redis

        redis = get_redis().client
        renewed = await redis.eval(
            _RENEW_GENERATION_LEASE,
            2,
            _lease_key(task_id),
            _task_meta_key(task_id),
            run_generation,
            _worker_id,
            max(10, ttl_seconds),
        )
        return int(renewed) == 1
    except Exception as exc:
        logger.warning("task lease renew failed task_id=%s: %s", task_id, exc)
        return False


async def get_task_lease_owner(task_id: str) -> str | None:
    if not task_id:
        return None
    try:
        from app.infrastructure.storage.redis import get_redis

        raw = await get_redis().client.get(_lease_key(task_id))
        if isinstance(raw, bytes):
            raw = raw.decode()
        if not raw:
            return None
        try:
            lease = json.loads(raw)
        except (TypeError, ValueError):
            return str(raw)
        owner = lease.get("worker_id") if isinstance(lease, dict) else None
        return str(owner) if owner else None
    except Exception as exc:
        logger.warning("task lease owner lookup failed task_id=%s: %s", task_id, exc)
        return None


async def release_task_lease(task_id: str, run_generation: int) -> None:
    if not task_id:
        return
    try:
        from app.infrastructure.storage.redis import get_redis

        redis = get_redis().client
        await redis.eval(
            _RELEASE_GENERATION_LEASE,
            1,
            _lease_key(task_id),
            run_generation,
            _worker_id,
        )
    except Exception as exc:
        logger.warning("task lease release failed task_id=%s: %s", task_id, exc)
