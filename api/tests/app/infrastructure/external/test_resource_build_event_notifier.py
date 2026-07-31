#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Redis build-event hints remain minimal, retryable, and disposable."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.infrastructure.external.resource_build_event_notifier import (
    RESOURCE_BUILD_EVENT_CHANNEL_PREFIX,
    RedisResourceBuildEventNotifier,
)


class _PubSub:
    def __init__(self, messages=(), *, subscribe_failure=False) -> None:
        self.messages = list(messages)
        self.subscribe_failure = subscribe_failure
        self.subscribed = []
        self.unsubscribed = []
        self.closed = False

    async def subscribe(self, channel):
        self.subscribed.append(channel)
        if self.subscribe_failure:
            raise ConnectionError("subscribe unavailable")

    async def unsubscribe(self, channel):
        self.unsubscribed.append(channel)

    async def get_message(self, **_kwargs):
        if self.messages:
            return self.messages.pop(0)
        return None

    async def aclose(self):
        self.closed = True


class _Redis:
    def __init__(
        self,
        *,
        failures: int = 0,
        messages=(),
        subscribe_failure: bool = False,
    ) -> None:
        self.failures = failures
        self.publish_calls = []
        self.pubsub_instance = _PubSub(
            messages,
            subscribe_failure=subscribe_failure,
        )

    async def publish(self, channel, payload):
        self.publish_calls.append((channel, payload))
        if len(self.publish_calls) <= self.failures:
            raise ConnectionError("redis unavailable")
        return 1

    def pubsub(self):
        return self.pubsub_instance


@pytest.mark.asyncio
async def test_publish_retries_and_never_contains_event_payload():
    client = _Redis(failures=2)
    notifier = RedisResourceBuildEventNotifier(
        SimpleNamespace(client=client),
        publish_attempts=3,
        retry_delay_seconds=0,
    )

    await notifier.publish("build-1", 7)

    assert len(client.publish_calls) == 3
    channel, raw = client.publish_calls[-1]
    assert channel == f"{RESOURCE_BUILD_EVENT_CHANNEL_PREFIX}build-1"
    assert json.loads(raw) == {"build_id": "build-1", "seq": 7}
    assert set(json.loads(raw)) == {"build_id", "seq"}


@pytest.mark.asyncio
async def test_publish_surfaces_exhausted_retry_for_service_to_degrade():
    client = _Redis(failures=3)
    notifier = RedisResourceBuildEventNotifier(
        SimpleNamespace(client=client),
        publish_attempts=3,
        retry_delay_seconds=0,
    )

    with pytest.raises(ConnectionError, match="unavailable"):
        await notifier.publish("build-1", 1)

    assert len(client.publish_calls) == 3


@pytest.mark.asyncio
async def test_subscription_ignores_malformed_messages_and_cleans_up():
    client = _Redis(
        messages=[
            {"type": "message", "data": '{"build_id":"build-1","seq":2,"payload":{}}'},
            {"type": "message", "data": "not-json"},
            {"type": "message", "data": '{"build_id":"build-1","seq":2}'},
        ]
    )
    notifier = RedisResourceBuildEventNotifier(SimpleNamespace(client=client))

    async with notifier.subscribe("build-1") as subscription:
        message = await subscription.wait(0.1)

    channel = f"{RESOURCE_BUILD_EVENT_CHANNEL_PREFIX}build-1"
    assert message == {"build_id": "build-1", "seq": 2}
    assert client.pubsub_instance.subscribed == [channel]
    assert client.pubsub_instance.unsubscribed == [channel]
    assert client.pubsub_instance.closed is True


@pytest.mark.asyncio
async def test_subscribe_failure_falls_back_to_heartbeat_polling_and_closes():
    client = _Redis(subscribe_failure=True)
    notifier = RedisResourceBuildEventNotifier(SimpleNamespace(client=client))

    async with notifier.subscribe("build-1") as subscription:
        assert await subscription.wait(0) is None

    assert client.pubsub_instance.closed is True
