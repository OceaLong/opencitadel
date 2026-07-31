#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Opt-in proof against a real Redis server for lossy build-event hints."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.infrastructure.external.resource_build_event_notifier import (
    RESOURCE_BUILD_EVENT_CHANNEL_PREFIX,
    RedisResourceBuildEventNotifier,
)
from app.infrastructure.external.task.task_lease import (
    TaskLeaseAcquireResult,
    get_task_lease_owner,
    release_task_lease,
    try_acquire_task_lease,
)
from core.config import get_settings


RUN_REDIS_INTEGRATION = (
    os.getenv("OPENCITADEL_RUN_REDIS_INTEGRATION") == "1"
)
pytestmark = pytest.mark.skipif(
    not RUN_REDIS_INTEGRATION,
    reason="set OPENCITADEL_RUN_REDIS_INTEGRATION=1 for real Redis proof",
)


def _client() -> Redis:
    settings = get_settings()
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password,
        decode_responses=True,
    )


async def _next_raw_message(pubsub, timeout: float):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        message = await pubsub.get_message(
            ignore_subscribe_messages=True,
            timeout=max(0.0, deadline - loop.time()),
        )
        if message is not None:
            return message
    return None


class _TrackedPubSub:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False
        self.fail_next_read = False

    async def subscribe(self, channel: str):
        self.subscribed.append(channel)
        return await self.delegate.subscribe(channel)

    async def unsubscribe(self, channel: str):
        self.unsubscribed.append(channel)
        return await self.delegate.unsubscribe(channel)

    async def get_message(self, **kwargs):
        if self.fail_next_read:
            self.fail_next_read = False
            raise ConnectionError("injected read disconnect")
        return await self.delegate.get_message(**kwargs)

    async def aclose(self):
        self.closed = True
        await self.delegate.aclose()


class _TrackedRedis:
    """Delegate all I/O to Redis while exposing pub/sub cleanup evidence."""

    def __init__(self, delegate: Redis) -> None:
        self.delegate = delegate
        self.pubsubs: list[_TrackedPubSub] = []

    async def publish(self, channel: str, payload: str):
        return await self.delegate.publish(channel, payload)

    def pubsub(self):
        tracked = _TrackedPubSub(self.delegate.pubsub())
        self.pubsubs.append(tracked)
        return tracked


class _CountingRedis:
    """Count real redis-py connection attempts to an unavailable endpoint."""

    def __init__(self, delegate: Redis) -> None:
        self.delegate = delegate
        self.publish_calls = 0

    async def publish(self, channel: str, payload: str):
        self.publish_calls += 1
        return await self.delegate.publish(channel, payload)

    def pubsub(self):
        return self.delegate.pubsub()


@pytest.mark.asyncio
async def test_real_redis_publish_uses_exact_minimal_wire_envelope():
    client = _client()
    build_id = f"redis-build-{uuid.uuid4()}"
    channel = f"{RESOURCE_BUILD_EVENT_CHANNEL_PREFIX}{build_id}"
    pubsub = client.pubsub()
    subscribed = False
    try:
        assert await client.ping() is True
        await pubsub.subscribe(channel)
        subscribed = True
        notifier = RedisResourceBuildEventNotifier(
            SimpleNamespace(client=client),
            retry_delay_seconds=0,
        )

        await notifier.publish(build_id, 7)
        message = await _next_raw_message(pubsub, timeout=2)

        assert message is not None
        assert message["type"] == "message"
        assert json.loads(message["data"]) == {
            "build_id": build_id,
            "seq": 7,
        }
    finally:
        if subscribed:
            await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_connectivity_failure_exhausts_and_polling_fallbacks():
    # Reserve a local port without listening. redis-py therefore exercises a
    # real, deterministic connection-refused path rather than a Redis mock.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
        client = Redis(
            host="127.0.0.1",
            port=port,
            socket_connect_timeout=0.1,
            socket_timeout=0.1,
            decode_responses=True,
        )
        counted_client = _CountingRedis(client)
        notifier = RedisResourceBuildEventNotifier(
            SimpleNamespace(client=counted_client),
            publish_attempts=3,
            retry_delay_seconds=0,
        )
        try:
            with pytest.raises(RedisError):
                await notifier.publish("unavailable-build", 1)
            assert counted_client.publish_calls == 3

            async with notifier.subscribe(
                "unavailable-build"
            ) as subscription:
                assert await subscription.wait(0) is None
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_read_failure_reconnect_no_history_and_cleanup():
    client = _client()
    tracked_client = _TrackedRedis(client)
    notifier = RedisResourceBuildEventNotifier(
        SimpleNamespace(client=tracked_client),
        retry_delay_seconds=0,
    )
    build_id = f"redis-build-{uuid.uuid4()}"
    channel = f"{RESOURCE_BUILD_EVENT_CHANNEL_PREFIX}{build_id}"
    try:
        assert await client.ping() is True

        # Redis Pub/Sub is deliberately non-durable: a pre-subscribe hint is
        # absent on the first connection.
        await notifier.publish(build_id, 1)
        async with notifier.subscribe(build_id) as subscription:
            assert await subscription.wait(0.1) is None

        # A read failure degrades to a PostgreSQL-polling heartbeat, and the
        # failed subscription still unsubscribes/closes on context exit.
        async with notifier.subscribe(build_id) as subscription:
            tracked_client.pubsubs[-1].fail_next_read = True
            assert await subscription.wait(0.01) is None

        # A fresh connection receives only the new hint.
        async with notifier.subscribe(build_id) as subscription:
            await notifier.publish(build_id, 2)
            assert await subscription.wait(2) == {
                "build_id": build_id,
                "seq": 2,
            }

        assert len(tracked_client.pubsubs) == 3
        for pubsub in tracked_client.pubsubs:
            assert pubsub.subscribed == [channel]
            assert pubsub.unsubscribed == [channel]
            assert pubsub.closed is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_kb_transport_lease_blocks_then_releases_reconcile():
    """Real Lua lease evidence for the worker reconciliation gate."""
    client = _client()
    build_id = f"kb-reconcile-lease-{uuid.uuid4()}"
    meta_key = f"task:meta:{build_id}"
    lease_key = f"task:execution:lease:{build_id}"
    try:
        assert await client.ping() is True
        await client.set(
            meta_key,
            json.dumps(
                {
                    "status": "pending",
                    "run_generation": 1,
                    "task_type": "kb_ingest",
                    "resource_id": "kb-real-lease",
                }
            ),
            ex=30,
        )
        with patch(
            "app.infrastructure.storage.redis.get_redis",
            return_value=SimpleNamespace(client=client),
        ):
            assert (
                await try_acquire_task_lease(build_id, 1, 30)
                is TaskLeaseAcquireResult.ACQUIRED
            )
            assert await get_task_lease_owner(build_id)
            assert (
                await try_acquire_task_lease(build_id, 1, 30)
                is TaskLeaseAcquireResult.SAME_GENERATION_CONFLICT
            )
            await release_task_lease(build_id, 1)
            assert await get_task_lease_owner(build_id) is None
    finally:
        await client.delete(meta_key, lease_key)
        await client.aclose()
