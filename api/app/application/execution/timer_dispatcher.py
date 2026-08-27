"""Timer orchestration over an injected durable dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.application.ports.execution import TimerDispatcherPort


@dataclass(frozen=True)
class TimerDispatchStats:
    claimed: int
    fired: int
    failed: int


class TimerDispatcher:
    def __init__(
        self,
        *,
        dispatcher: TimerDispatcherPort,
        claim_ttl: timedelta = timedelta(seconds=30),
    ) -> None:
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        self._dispatcher = dispatcher
        self._claim_ttl = claim_ttl

    async def fire_due(self, *, limit: int, now: datetime) -> TimerDispatchStats:
        result = await self._dispatcher.fire_due(
            limit=limit,
            now=now,
            claim_ttl=self._claim_ttl,
        )
        return TimerDispatchStats(result.claimed, result.fired, result.failed)


__all__ = ["TimerDispatchStats", "TimerDispatcher"]
