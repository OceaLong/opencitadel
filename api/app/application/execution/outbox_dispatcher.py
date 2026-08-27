"""Durable outbox orchestration over injected persistence and wake-up ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.application.ports.execution import (
    OutboxClaim,
    OutboxStorePort,
    WakeupMessage,
    WakeupPublisherPort,
)


@dataclass(frozen=True)
class DispatchStats:
    claimed: int
    published: int
    failed: int


class OutboxDispatcher:
    def __init__(
        self,
        *,
        store: OutboxStorePort,
        publisher: WakeupPublisherPort,
        claim_ttl: timedelta = timedelta(seconds=30),
        base_retry_delay: timedelta = timedelta(seconds=1),
        max_retry_delay: timedelta = timedelta(minutes=5),
    ) -> None:
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        if base_retry_delay <= timedelta(0):
            raise ValueError("base_retry_delay must be positive")
        if max_retry_delay < base_retry_delay:
            raise ValueError("max_retry_delay must not be smaller than base delay")
        self._store = store
        self._publisher = publisher
        self._claim_ttl = claim_ttl
        self._base_retry_delay = base_retry_delay
        self._max_retry_delay = max_retry_delay

    async def dispatch_batch(self, *, limit: int, now: datetime) -> DispatchStats:
        claims = await self._store.claim_batch(
            limit=limit,
            now=now,
            claim_ttl=self._claim_ttl,
        )
        published = failed = 0
        for claim in claims:
            try:
                await self._publisher.publish(
                    WakeupMessage(
                        destination=claim.destination,
                        dedupe_key=claim.dedupe_key,
                        event_position=claim.event_position,
                    )
                )
            except (OSError, RuntimeError, ValueError) as error:
                acknowledged = await self._mark_failed(
                    claim,
                    now=now,
                    error_type=type(error).__name__,
                )
                failed += int(acknowledged)
                continue
            acknowledged = await self._store.mark_delivered(claim, now=now)
            published += int(acknowledged)
            failed += int(not acknowledged)
        return DispatchStats(len(claims), published, failed)

    async def _mark_failed(
        self,
        claim: OutboxClaim,
        *,
        now: datetime,
        error_type: str,
    ) -> bool:
        return await self._store.mark_failed(
            claim,
            now=now,
            error_type=error_type,
            base_retry_delay=self._base_retry_delay,
            max_retry_delay=self._max_retry_delay,
        )


__all__ = ["DispatchStats", "OutboxDispatcher"]
