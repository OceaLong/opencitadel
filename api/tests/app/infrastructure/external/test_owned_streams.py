from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.application.ports.coordination import RedisConnectivity
from app.composition.tasks import TaskSupervisor
from app.infrastructure.adapters.redis_capabilities import (
    RedisNotificationStreamFactory,
    RedisSessionListStreamFactory,
)
from app.infrastructure.external.session_list_notifier import (
    DebouncedSessionListPublisher,
)


class _PubSub:
    def __init__(
        self,
        *,
        subscribe_error: Exception | None = None,
        unsubscribe_error: Exception | None = None,
    ) -> None:
        self.subscribe_error = subscribe_error
        self.unsubscribe_error = unsubscribe_error
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        if self.subscribe_error is not None:
            raise self.subscribe_error
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)
        if self.unsubscribe_error is not None:
            raise self.unsubscribe_error

    async def aclose(self) -> None:
        self.closed = True

    async def get_message(self, **_kwargs: Any):
        return None


class _Redis:
    def __init__(self, pubsub: _PubSub) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> _PubSub:
        return self._pubsub


@pytest.mark.asyncio
async def test_notification_stream_disconnect_closes_pubsub() -> None:
    pubsub = _PubSub()
    factory = RedisNotificationStreamFactory(_Redis(pubsub))

    async with factory.open("user-1") as stream:
        poll = await stream.poll(timeout_seconds=0)
        assert poll.payload is None

    assert pubsub.subscribed == ["notify:user-1"]
    assert pubsub.unsubscribed == ["notify:user-1"]
    assert pubsub.closed is True


@pytest.mark.asyncio
async def test_stream_subscribe_failure_still_closes_pubsub() -> None:
    pubsub = _PubSub(subscribe_error=ConnectionError("redis unavailable"))
    factory = RedisSessionListStreamFactory(_Redis(pubsub))

    with pytest.raises(ConnectionError, match="redis unavailable"):
        async with factory.open():
            pass

    assert pubsub.closed is True


@pytest.mark.asyncio
async def test_stream_unsubscribe_failure_still_closes_pubsub() -> None:
    pubsub = _PubSub(unsubscribe_error=ConnectionError("redis unavailable"))
    factory = RedisSessionListStreamFactory(_Redis(pubsub))

    with pytest.raises(ConnectionError, match="redis unavailable"):
        async with factory.open():
            pass

    assert pubsub.closed is True


@pytest.mark.asyncio
async def test_session_list_debounce_is_instance_owned_and_supervised() -> None:
    published = asyncio.Event()

    class _Publisher:
        def __init__(self) -> None:
            self.calls = 0

        async def publish(self, _channel: str, _payload: str) -> RedisConnectivity:
            self.calls += 1
            published.set()
            return RedisConnectivity(True, None)

    low_level = _Publisher()
    supervisor = TaskSupervisor()
    publisher = DebouncedSessionListPublisher(
        publisher=low_level,
        supervisor=supervisor,
        delay_seconds=0.01,
    )

    await publisher.publish_changed()
    await publisher.publish_changed()
    await asyncio.wait_for(published.wait(), timeout=1)
    await supervisor.stop()

    assert low_level.calls == 1
    assert supervisor.pending_names == ()
