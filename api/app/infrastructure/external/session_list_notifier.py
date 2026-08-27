"""Instance-owned, supervised session-list hint debouncing."""

from __future__ import annotations

import asyncio
import logging

from app.application.ports.coordination import RedisConnectivity
from app.application.ports.streams import (
    SESSION_LIST_HINT_CHANNEL,
    HintPublisherPort,
)
from app.composition.tasks import TaskSupervisor

logger = logging.getLogger(__name__)


class DebouncedSessionListPublisher:
    def __init__(
        self,
        *,
        publisher: HintPublisherPort,
        supervisor: TaskSupervisor,
        delay_seconds: float = 0.2,
    ) -> None:
        if delay_seconds < 0:
            raise ValueError("session-list debounce delay must not be negative")
        self._publisher = publisher
        self._supervisor = supervisor
        self._delay_seconds = delay_seconds
        self._generation = 0

    async def publish_changed(self) -> RedisConnectivity:
        self._generation += 1
        generation = self._generation

        async def delayed() -> None:
            await asyncio.sleep(self._delay_seconds)
            if generation != self._generation:
                return
            connectivity = await self._publisher.publish(SESSION_LIST_HINT_CHANNEL, "1")
            if not connectivity.available:
                logger.debug(
                    "Session list hint unavailable: %s",
                    connectivity.error_key,
                )

        await self._supervisor.start_transient(
            f"session-list-hint:{generation}",
            delayed,
        )
        return RedisConnectivity(True, None)


__all__ = ["DebouncedSessionListPublisher"]
