"""Retention windows and batching for the execution queue GC (K2-5/D7)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.application.services.execution_queue_retention_service import (
    ExecutionQueueRetentionService,
)

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)


class _Store:
    def __init__(self) -> None:
        self.calls: dict[str, tuple[datetime, int]] = {}

    async def purge_inbox(self, *, before, limit):
        self.calls["inbox"] = (before, limit)
        return 3

    async def purge_inbox_dead_letters(self, *, before, limit):
        self.calls["inbox_dead_letters"] = (before, limit)
        return 5

    async def purge_outbox(self, *, before, limit):
        self.calls["outbox"] = (before, limit)
        return 2

    async def purge_timers(self, *, before, limit):
        self.calls["timers"] = (before, limit)
        return 1

    async def purge_activities(self, *, before, limit):
        self.calls["activities"] = (before, limit)
        return 4


def _service(store: _Store | None = None, **overrides) -> ExecutionQueueRetentionService:
    parameters = {
        "inbox_retention_days": 7,
        "inbox_dead_letter_retention_days": 30,
        "outbox_retention_days": 7,
        "timer_retention_days": 30,
        "activity_retention_days": 30,
        "batch_size": 500,
        **overrides,
    }
    return ExecutionQueueRetentionService(store or _Store(), **parameters)


@pytest.mark.asyncio
async def test_each_queue_is_purged_with_its_own_retention_cutoff() -> None:
    store = _Store()
    service = _service(store)

    assert service.enabled is True
    purged = await service.purge_expired(now=NOW)

    assert purged == {
        "inbox": 3,
        "inbox_dead_letters": 5,
        "outbox": 2,
        "timers": 1,
        "activities": 4,
    }
    assert store.calls["inbox"] == (NOW - timedelta(days=7), 500)
    # Dead-lettered rows are diagnostics: retained longer than settled rows,
    # but still bounded — they previously grew forever.
    assert store.calls["inbox_dead_letters"] == (NOW - timedelta(days=30), 500)
    assert store.calls["outbox"] == (NOW - timedelta(days=7), 500)
    assert store.calls["timers"] == (NOW - timedelta(days=30), 500)
    assert store.calls["activities"] == (NOW - timedelta(days=30), 500)


@pytest.mark.asyncio
async def test_zero_retention_disables_that_queue_only() -> None:
    store = _Store()
    service = _service(
        store,
        inbox_retention_days=0,
        inbox_dead_letter_retention_days=0,
        timer_retention_days=0,
        activity_retention_days=0,
        batch_size=10,
    )

    purged = await service.purge_expired(now=NOW)

    assert purged == {
        "inbox": 0,
        "inbox_dead_letters": 0,
        "outbox": 2,
        "timers": 0,
        "activities": 0,
    }
    assert set(store.calls) == {"outbox"}


def test_all_zero_retention_disables_the_service() -> None:
    service = _service(
        inbox_retention_days=0,
        inbox_dead_letter_retention_days=0,
        outbox_retention_days=0,
        timer_retention_days=0,
        activity_retention_days=0,
    )
    assert service.enabled is False


def test_invalid_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="inbox_retention_days"):
        _service(inbox_retention_days=-1)
    with pytest.raises(ValueError, match="inbox_dead_letter_retention_days"):
        _service(inbox_dead_letter_retention_days=-1)
    with pytest.raises(ValueError, match="batch_size"):
        _service(batch_size=0)
