#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, patch

from app.infrastructure.external.task import task_lease


async def _test_try_acquire_task_lease_success():
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    with patch("app.infrastructure.storage.redis.get_redis") as get_redis:
        get_redis.return_value.client = redis
        assert await task_lease.try_acquire_task_lease("task-1", 60) is True
    redis.set.assert_awaited_once()
    call_kwargs = redis.set.await_args.kwargs
    assert call_kwargs.get("nx") is True
    assert call_kwargs.get("ex") == 60


def test_try_acquire_task_lease_success():
    asyncio.run(_test_try_acquire_task_lease_success())


async def _test_try_acquire_task_lease_conflict():
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=False)
    with patch("app.infrastructure.storage.redis.get_redis") as get_redis:
        get_redis.return_value.client = redis
        assert await task_lease.try_acquire_task_lease("task-1", 60) is False


def test_try_acquire_task_lease_conflict():
    asyncio.run(_test_try_acquire_task_lease_conflict())


async def _test_release_task_lease_deletes_key_when_owner_matches():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=task_lease._worker_id)
    redis.delete = AsyncMock(return_value=1)
    with patch("app.infrastructure.storage.redis.get_redis") as get_redis:
        get_redis.return_value.client = redis
        await task_lease.release_task_lease("task-1")
    redis.delete.assert_awaited_once()


def test_release_task_lease_deletes_key_when_owner_matches():
    asyncio.run(_test_release_task_lease_deletes_key_when_owner_matches())


async def _test_renew_task_lease_extends_when_owner_matches():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=task_lease._worker_id)
    redis.expire = AsyncMock(return_value=True)
    with patch("app.infrastructure.storage.redis.get_redis") as get_redis:
        get_redis.return_value.client = redis
        assert await task_lease.renew_task_lease("task-1", 60) is True
    redis.expire.assert_awaited_once()


def test_renew_task_lease_extends_when_owner_matches():
    asyncio.run(_test_renew_task_lease_extends_when_owner_matches())
