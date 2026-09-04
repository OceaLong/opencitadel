"""Scheduled lifecycle cleanup for the execution kernel's durable queues (D7).

The inbox/outbox/timer/activity tables are append-heavy operational queues:
settled rows serve no runtime purpose but keep growing the tables (and every
claim scan) forever. The scheduler's leader tick calls this service to delete
them in bounded batches once they age past their per-queue retention window.

Activity rows carry one hard constraint: an active Run's decision source
rehydrates ``decision_payload`` from settled activity rows, so the store only
purges activity rows whose owning Run projection is already terminal — this
service just picks the cutoff, the invariant lives in the store.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol


class ExecutionQueueRetentionStorePort(Protocol):
    async def purge_inbox(self, *, before: datetime, limit: int) -> int: ...

    async def purge_inbox_dead_letters(self, *, before: datetime, limit: int) -> int: ...

    async def purge_outbox(self, *, before: datetime, limit: int) -> int: ...

    async def purge_timers(self, *, before: datetime, limit: int) -> int: ...

    async def purge_activities(self, *, before: datetime, limit: int) -> int: ...


class ExecutionQueueRetentionService:
    def __init__(
        self,
        store: ExecutionQueueRetentionStorePort,
        *,
        inbox_retention_days: int,
        inbox_dead_letter_retention_days: int,
        outbox_retention_days: int,
        timer_retention_days: int,
        activity_retention_days: int,
        batch_size: int = 500,
    ) -> None:
        for name, value in (
            ("inbox_retention_days", inbox_retention_days),
            ("inbox_dead_letter_retention_days", inbox_dead_letter_retention_days),
            ("outbox_retention_days", outbox_retention_days),
            ("timer_retention_days", timer_retention_days),
            ("activity_retention_days", activity_retention_days),
        ):
            if value < 0:
                raise ValueError(f"{name} must not be negative")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._store = store
        self._inbox_retention_days = inbox_retention_days
        self._inbox_dead_letter_retention_days = inbox_dead_letter_retention_days
        self._outbox_retention_days = outbox_retention_days
        self._timer_retention_days = timer_retention_days
        self._activity_retention_days = activity_retention_days
        self._batch_size = batch_size

    @property
    def enabled(self) -> bool:
        return any(
            days > 0
            for days in (
                self._inbox_retention_days,
                self._inbox_dead_letter_retention_days,
                self._outbox_retention_days,
                self._timer_retention_days,
                self._activity_retention_days,
            )
        )

    async def purge_expired(self, *, now: datetime) -> dict[str, int]:
        """Purge one bounded batch per queue; a retention of 0 disables it."""
        purged: dict[str, int] = {}
        plan = (
            ("inbox", self._inbox_retention_days, self._store.purge_inbox),
            (
                "inbox_dead_letters",
                self._inbox_dead_letter_retention_days,
                self._store.purge_inbox_dead_letters,
            ),
            ("outbox", self._outbox_retention_days, self._store.purge_outbox),
            ("timers", self._timer_retention_days, self._store.purge_timers),
            ("activities", self._activity_retention_days, self._store.purge_activities),
        )
        for name, retention_days, purge in plan:
            if retention_days <= 0:
                purged[name] = 0
                continue
            purged[name] = await purge(
                before=now - timedelta(days=retention_days),
                limit=self._batch_size,
            )
        return purged


__all__ = [
    "ExecutionQueueRetentionService",
    "ExecutionQueueRetentionStorePort",
]
