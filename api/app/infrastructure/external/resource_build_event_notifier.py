#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Loss-tolerant Redis hints for committed resource-build events."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from app.infrastructure.storage.redis import RedisClient

logger = logging.getLogger(__name__)

RESOURCE_BUILD_EVENT_CHANNEL_PREFIX = "resource-build-events:"


class RedisResourceBuildEventSubscription:
    def __init__(self, pubsub: Any) -> None:
        self._pubsub = pubsub

    async def wait(self, timeout: float) -> dict[str, object] | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=remaining,
                )
            except Exception as exc:
                logger.warning(
                    "Resource-build subscription read failed; polling "
                    "PostgreSQL on heartbeat: %s",
                    exc,
                )
                await asyncio.sleep(max(0.0, remaining))
                return None
            if message is None:
                return None
            if message.get("type") != "message":
                continue
            try:
                raw = message.get("data")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                payload = json.loads(raw)
                build_id = payload["build_id"]
                seq = payload["seq"]
                if (
                    not isinstance(build_id, str)
                    or not isinstance(seq, int)
                    or isinstance(seq, bool)
                    or seq < 1
                    or set(payload) != {"build_id", "seq"}
                ):
                    raise ValueError("invalid build event notification")
                return {"build_id": build_id, "seq": seq}
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                logger.warning("Ignoring malformed resource-build notification")


class PollingResourceBuildEventSubscription:
    """Redis-down fallback; callers refetch PostgreSQL after each wait."""

    async def wait(self, timeout: float) -> None:
        await asyncio.sleep(max(0.0, timeout))
        return None


class RedisResourceBuildEventNotifier:
    def __init__(
        self,
        redis: RedisClient,
        *,
        publish_attempts: int = 3,
        retry_delay_seconds: float = 0.05,
    ) -> None:
        self._redis = redis
        self._publish_attempts = max(1, publish_attempts)
        self._retry_delay_seconds = max(0.0, retry_delay_seconds)

    async def publish(self, build_id: str, seq: int) -> None:
        if not build_id or seq < 1:
            raise ValueError("build notification requires build_id and seq")
        payload = json.dumps(
            {"build_id": build_id, "seq": seq},
            separators=(",", ":"),
            sort_keys=True,
        )
        channel = f"{RESOURCE_BUILD_EVENT_CHANNEL_PREFIX}{build_id}"
        for attempt in range(1, self._publish_attempts + 1):
            try:
                await self._redis.client.publish(channel, payload)
                return
            except Exception:
                if attempt == self._publish_attempts:
                    raise
                await asyncio.sleep(
                    self._retry_delay_seconds * (2 ** (attempt - 1))
                )

    @asynccontextmanager
    async def subscribe(
        self,
        build_id: str,
    ) -> AsyncIterator[
        RedisResourceBuildEventSubscription
        | PollingResourceBuildEventSubscription
    ]:
        channel = f"{RESOURCE_BUILD_EVENT_CHANNEL_PREFIX}{build_id}"
        pubsub = None
        try:
            pubsub = self._redis.client.pubsub()
            await pubsub.subscribe(channel)
        except Exception as exc:
            logger.warning(
                "Resource-build Redis subscribe failed; falling back to "
                "PostgreSQL polling build=%s: %s",
                build_id,
                exc,
            )
            if pubsub is not None:
                await _close_pubsub(pubsub)
            yield PollingResourceBuildEventSubscription()
            return

        try:
            yield RedisResourceBuildEventSubscription(pubsub)
        finally:
            try:
                await pubsub.unsubscribe(channel)
            except Exception as exc:
                logger.warning(
                    "Resource-build Redis unsubscribe failed build=%s: %s",
                    build_id,
                    exc,
                )
            await _close_pubsub(pubsub)


async def _close_pubsub(pubsub: Any) -> None:
    try:
        close = getattr(pubsub, "aclose", None) or getattr(
            pubsub,
            "close",
        )
        await close()
    except Exception as exc:
        logger.warning("Resource-build Redis pubsub close failed: %s", exc)
