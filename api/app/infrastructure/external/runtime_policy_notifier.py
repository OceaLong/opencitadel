"""Supervisable Runtime Policy hints over owned Redis streams."""

from __future__ import annotations

import logging

from redis.asyncio import Redis

from app.application.ports.streams import (
    RUNTIME_POLICY_HINT_CHANNEL,
    RuntimePolicyHintStreamFactory,
)
from app.application.services.runtime_policy_reader import RuntimePolicyReader
from app.infrastructure.adapters.redis_capabilities import RedisHintPublisher

logger = logging.getLogger(__name__)

RUNTIME_POLICY_CHANGED_CHANNEL = RUNTIME_POLICY_HINT_CHANNEL


class RuntimePolicyHintPublisher:
    def __init__(self, *, redis: Redis) -> None:
        self._publisher = RedisHintPublisher(redis)

    async def publish_changed(self, head_version: int) -> None:
        if isinstance(head_version, bool) or head_version < 1:
            raise ValueError("head_version must be a positive integer")
        connectivity = await self._publisher.publish(
            RUNTIME_POLICY_CHANGED_CHANNEL,
            str(head_version),
        )
        if not connectivity.available:
            logger.warning(
                "Runtime Policy hint publication failed: %s",
                connectivity.error_key,
            )


class RuntimePolicyHintListener:
    """One listener run; its restart and cancellation belong to a supervisor."""

    def __init__(
        self,
        *,
        streams: RuntimePolicyHintStreamFactory,
        reader: RuntimePolicyReader,
    ) -> None:
        self._streams = streams
        self._reader = reader

    async def run(self) -> None:
        async with self._streams.open() as stream:
            while True:
                poll = await stream.poll(timeout_seconds=30)
                if not poll.connectivity.available:
                    raise OSError(poll.connectivity.error_key or "redis_unavailable")
                if poll.payload is None:
                    continue
                try:
                    head_version = int(poll.payload)
                except (TypeError, ValueError):
                    logger.warning("Ignored invalid Runtime Policy hint payload")
                    continue
                if head_version < 1:
                    logger.warning("Ignored non-positive Runtime Policy head hint")
                    continue
                await self._reader.handle_hint()


__all__ = [
    "RUNTIME_POLICY_CHANGED_CHANNEL",
    "RuntimePolicyHintListener",
    "RuntimePolicyHintPublisher",
]
