#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json
import re
from unittest.mock import AsyncMock, patch

from app.infrastructure.external.task import task_lease
from app.infrastructure.external.task.task_state import TaskStateService


class _AcquireScriptRedis:
    """Interpret the acquire script against in-memory Redis JSON values."""

    def __init__(self, *, meta, lease=None):
        self.meta = meta
        self.lease = lease

    @staticmethod
    def _branch_result(script, condition_prefix):
        match = re.search(
            rf"if {condition_prefix}.*?then\s+return\s+(-?\d+)",
            script,
            re.DOTALL,
        )
        assert match is not None
        return int(match.group(1))

    async def eval(
            self,
            script,
            num_keys,
            _lease_key,
            _meta_key,
            requested_generation,
            worker_id,
            _ttl,
    ):
        assert num_keys == 2
        assert "opencitadel:acquire-generation-lease" in script
        if self.meta is None:
            return -1
        current_generation = int(self.meta.get("run_generation", 1))
        if requested_generation < current_generation:
            return -2
        if requested_generation > current_generation:
            return -3
        if self.meta.get("status") in {"done", "cancelled", "failed"}:
            return -4
        if self.lease is not None:
            try:
                lease = json.loads(self.lease)
            except (TypeError, ValueError):
                return self._branch_result(script, "not decoded")
            if not isinstance(lease, dict):
                return self._branch_result(script, "not lease_generation")
            try:
                lease_generation = int(lease.get("run_generation"))
            except (TypeError, ValueError):
                return self._branch_result(script, "not lease_generation")
            if lease_generation == requested_generation:
                return 0
            if lease_generation > requested_generation:
                return -5
        self.lease = json.dumps(
            {
                "worker_id": worker_id,
                "run_generation": requested_generation,
            }
        )
        return 1


def test_established_lease_and_metadata_keys_remain_upgrade_compatible():
    task_id = "task-1"

    assert TaskStateService.meta_key(task_id) == "task:meta:task-1"
    assert task_lease._lease_key(task_id) == "task:execution:lease:task-1"


async def _classify_script_state(*, meta, lease=None):
    redis = _AcquireScriptRedis(meta=meta, lease=lease)
    with patch("app.infrastructure.storage.redis.get_redis") as get_redis:
        get_redis.return_value.client = redis
        result = await task_lease.try_acquire_task_lease("task-1", 2, 60)
    return result, redis


def test_acquire_script_requeues_unreadable_or_untyped_lease_state():
    async def run():
        meta = {"run_generation": 2, "status": "pending"}
        malformed_leases = [
            "{not-json",
            json.dumps(["worker-a", 2]),
            json.dumps({"worker_id": "worker-a"}),
            json.dumps(
                {
                    "worker_id": "worker-a",
                    "run_generation": "not-a-number",
                }
            ),
        ]
        for lease in malformed_leases:
            result, _redis = await _classify_script_state(
                meta=meta,
                lease=lease,
            )
            assert result.value == "ERROR"

    asyncio.run(run())


def test_acquire_script_preserves_terminal_conflict_and_old_lease_behavior():
    async def run():
        terminal, _ = await _classify_script_state(
            meta={"run_generation": 2, "status": "done"},
        )
        assert terminal.value == "TERMINAL"

        conflict, _ = await _classify_script_state(
            meta={"run_generation": 2, "status": "pending"},
            lease=json.dumps(
                {"worker_id": "worker-a", "run_generation": 2}
            ),
        )
        assert conflict.value == "SAME_GENERATION_CONFLICT"

        acquired, redis = await _classify_script_state(
            meta={"run_generation": 2, "status": "pending"},
            lease=json.dumps(
                {"worker_id": "old-worker", "run_generation": 1}
            ),
        )
        assert acquired.value == "ACQUIRED"
        assert json.loads(redis.lease)["run_generation"] == 2

    asyncio.run(run())


async def _test_try_acquire_task_lease_success():
    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=1)
    with patch("app.infrastructure.storage.redis.get_redis") as get_redis:
        get_redis.return_value.client = redis
        result = await task_lease.try_acquire_task_lease("task-1", 2, 60)
        assert result.value == "ACQUIRED"
    redis.eval.assert_awaited_once()
    assert "opencitadel:acquire-generation-lease" in redis.eval.await_args.args[0]
    assert redis.eval.await_args.args[-3:] == (2, task_lease._worker_id, 60)


def test_try_acquire_task_lease_success():
    asyncio.run(_test_try_acquire_task_lease_success())


async def _test_try_acquire_task_lease_conflict():
    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=0)
    with patch("app.infrastructure.storage.redis.get_redis") as get_redis:
        get_redis.return_value.client = redis
        result = await task_lease.try_acquire_task_lease("task-1", 2, 60)
        assert result.value == "SAME_GENERATION_CONFLICT"


def test_try_acquire_task_lease_conflict():
    asyncio.run(_test_try_acquire_task_lease_conflict())


async def _test_missing_task_cannot_acquire_generation_lease():
    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=-1)
    with patch("app.infrastructure.storage.redis.get_redis") as get_redis:
        get_redis.return_value.client = redis
        result = await task_lease.try_acquire_task_lease(
            "missing-task",
            1,
            60,
        )
        assert result.value == "MISSING_TASK"


def test_missing_task_cannot_acquire_generation_lease():
    asyncio.run(_test_missing_task_cannot_acquire_generation_lease())


async def _test_acquire_classifies_generation_status_and_future_lease():
    cases = [
        (-2, "STALE_GENERATION"),
        (-3, "FUTURE_GENERATION"),
        (-4, "TERMINAL"),
        (-5, "FUTURE_LEASE"),
    ]
    for lua_result, expected in cases:
        redis = AsyncMock()
        redis.eval = AsyncMock(return_value=lua_result)
        with patch("app.infrastructure.storage.redis.get_redis") as get_redis:
            get_redis.return_value.client = redis
            result = await task_lease.try_acquire_task_lease(
                "task-1",
                2,
                60,
            )
        assert result.value == expected


def test_acquire_classifies_generation_status_and_future_lease():
    asyncio.run(_test_acquire_classifies_generation_status_and_future_lease())


async def _test_acquire_redis_error_is_distinct():
    redis = AsyncMock()
    redis.eval = AsyncMock(side_effect=RuntimeError("redis unavailable"))
    with patch("app.infrastructure.storage.redis.get_redis") as get_redis:
        get_redis.return_value.client = redis
        result = await task_lease.try_acquire_task_lease("task-1", 2, 60)
    assert result.value == "ERROR"


def test_acquire_redis_error_is_distinct():
    asyncio.run(_test_acquire_redis_error_is_distinct())


async def _test_release_task_lease_deletes_key_when_owner_matches():
    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=1)
    with patch("app.infrastructure.storage.redis.get_redis") as get_redis:
        get_redis.return_value.client = redis
        await task_lease.release_task_lease("task-1", 2)
    redis.eval.assert_awaited_once()
    assert "opencitadel:release-generation-lease" in redis.eval.await_args.args[0]


def test_release_task_lease_deletes_key_when_owner_matches():
    asyncio.run(_test_release_task_lease_deletes_key_when_owner_matches())


async def _test_renew_task_lease_extends_when_owner_matches():
    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=1)
    with patch("app.infrastructure.storage.redis.get_redis") as get_redis:
        get_redis.return_value.client = redis
        assert await task_lease.renew_task_lease("task-1", 2, 60) is True
    redis.eval.assert_awaited_once()
    assert "opencitadel:renew-generation-lease" in redis.eval.await_args.args[0]


def test_renew_task_lease_extends_when_owner_matches():
    asyncio.run(_test_renew_task_lease_extends_when_owner_matches())
