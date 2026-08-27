"""Low-cardinality operational metrics for the sole execution kernel."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from prometheus_client import Counter, Gauge
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.observability import ExecutionMetricsSnapshot
from app.domain.execution.commands import normalize_utc
from app.infrastructure.execution.models import (
    ExecutionActivityTaskORM,
    ExecutionCommandInboxORM,
    ExecutionEventORM,
    ExecutionOutboxORM,
    ExecutionProjectorCheckpointORM,
    ExecutionScheduledCommandORM,
)

INBOX_STATUSES = ("received", "processing", "accepted", "rejected", "dead_lettered")
ACTIVITY_STATUSES = (
    "pending",
    "claimed",
    "call_started",
    "succeeded",
    "failed",
    "unknown",
    "dead_lettered",
)
PROJECTOR_NAMES = ("formal",)
REPLAY_FAILURE_REASONS = (
    "event_hash_mismatch",
    "invalid_event_sequence",
    "snapshot_hash_mismatch",
    "projection_hash_mismatch",
)

EXECUTION_INBOX_ROWS = Gauge(
    "execution_inbox_rows", "Durable execution commands by Inbox status", ("status",)
)
EXECUTION_INBOX_OLDEST_AGE_SECONDS = Gauge(
    "execution_inbox_oldest_age_seconds",
    "Age of the oldest execution command by Inbox status",
    ("status",),
)
EXECUTION_OUTBOX_LAG_SECONDS = Gauge(
    "execution_outbox_lag_seconds", "Age of the oldest undelivered wakeup"
)
EXECUTION_OUTBOX_REDELIVERIES = Gauge(
    "execution_outbox_redelivery_rows", "Undelivered wakeups attempted more than once"
)
EXECUTION_TIMER_LAG_SECONDS = Gauge(
    "execution_timer_lag_seconds", "Overdue age of the oldest pending timer"
)
EXECUTION_ACTIVITY_ROWS = Gauge(
    "execution_activity_rows", "Durable Activities by status", ("status",)
)
EXECUTION_ACTIVITY_OLDEST_AGE_SECONDS = Gauge(
    "execution_activity_oldest_age_seconds",
    "Age of the oldest Activity by status",
    ("status",),
)
EXECUTION_PROJECTOR_CURSOR_LAG = Gauge(
    "execution_projector_cursor_lag",
    "Global event positions behind the slowest owner checkpoint",
    ("projector",),
)
EXECUTION_OPTIMISTIC_CONFLICTS = Counter(
    "execution_optimistic_conflicts_total",
    "Event Store optimistic concurrency conflicts",
)
EXECUTION_REPLAY_FAILURES = Counter(
    "execution_replay_failures_total",
    "Replay, hash-chain and projection integrity failures",
    ("reason",),
)


class ExecutionMetrics:
    async def refresh(
        self,
        session: AsyncSession,
        *,
        now: datetime,
    ) -> ExecutionMetricsSnapshot:
        resolved_now = normalize_utc(now)
        inbox_rows, inbox_ages = await self._status_metrics(
            session,
            model=ExecutionCommandInboxORM,
            statuses=INBOX_STATUSES,
            timestamp=ExecutionCommandInboxORM.received_at,
            row_metric=EXECUTION_INBOX_ROWS,
            age_metric=EXECUTION_INBOX_OLDEST_AGE_SECONDS,
            now=resolved_now,
        )

        oldest_outbox = await session.scalar(
            select(func.min(ExecutionOutboxORM.available_at)).where(
                ExecutionOutboxORM.delivered_at.is_(None)
            )
        )
        outbox_lag = self._age(resolved_now, oldest_outbox)
        outbox_redeliveries = int(
            await session.scalar(
                select(func.count())
                .select_from(ExecutionOutboxORM)
                .where(
                    ExecutionOutboxORM.delivered_at.is_(None),
                    ExecutionOutboxORM.attempts > 1,
                )
            )
            or 0
        )
        EXECUTION_OUTBOX_LAG_SECONDS.set(outbox_lag)
        EXECUTION_OUTBOX_REDELIVERIES.set(outbox_redeliveries)

        oldest_timer = await session.scalar(
            select(func.min(ExecutionScheduledCommandORM.due_at)).where(
                ExecutionScheduledCommandORM.status == "pending",
                ExecutionScheduledCommandORM.cancellation_event_id.is_(None),
                ExecutionScheduledCommandORM.due_at <= resolved_now,
            )
        )
        timer_lag = self._age(resolved_now, oldest_timer)
        EXECUTION_TIMER_LAG_SECONDS.set(timer_lag)

        activity_rows, activity_ages = await self._status_metrics(
            session,
            model=ExecutionActivityTaskORM,
            statuses=ACTIVITY_STATUSES,
            timestamp=ExecutionActivityTaskORM.created_at,
            row_metric=EXECUTION_ACTIVITY_ROWS,
            age_metric=EXECUTION_ACTIVITY_OLDEST_AGE_SECONDS,
            now=resolved_now,
        )

        event_head = int(await session.scalar(select(func.max(ExecutionEventORM.position))) or 0)
        projector_lag: dict[str, int] = {}
        for projector_name in PROJECTOR_NAMES:
            slowest_cursor = await session.scalar(
                select(func.min(ExecutionProjectorCheckpointORM.last_position)).where(
                    ExecutionProjectorCheckpointORM.projector_name == projector_name
                )
            )
            lag = max(0, event_head - int(slowest_cursor or 0))
            projector_lag[projector_name] = lag
            EXECUTION_PROJECTOR_CURSOR_LAG.labels(projector=projector_name).set(lag)

        return ExecutionMetricsSnapshot(
            inbox_rows=inbox_rows,
            inbox_oldest_age_seconds=inbox_ages,
            outbox_lag_seconds=outbox_lag,
            outbox_redeliveries=outbox_redeliveries,
            timer_lag_seconds=timer_lag,
            activity_rows=activity_rows,
            activity_oldest_age_seconds=activity_ages,
            projector_cursor_lag=projector_lag,
        )

    @classmethod
    async def _status_metrics(
        cls,
        session: AsyncSession,
        *,
        model: type[Any],
        statuses: tuple[str, ...],
        timestamp: Any,
        row_metric: Gauge,
        age_metric: Gauge,
        now: datetime,
    ) -> tuple[dict[str, int], dict[str, float]]:
        rows = dict.fromkeys(statuses, 0)
        ages = dict.fromkeys(statuses, 0.0)
        results = (
            await session.execute(
                select(model.status, func.count(), func.min(timestamp)).group_by(model.status)
            )
        ).all()
        for status, count, oldest in results:
            if status in rows:
                rows[status] = int(count)
                ages[status] = cls._age(now, oldest)
        for status in statuses:
            row_metric.labels(status=status).set(rows[status])
            age_metric.labels(status=status).set(ages[status])
        return rows, ages

    @staticmethod
    def _age(now: datetime, occurred_at: datetime | None) -> float:
        if occurred_at is None:
            return 0.0
        return max(0.0, (now - normalize_utc(occurred_at)).total_seconds())


def record_optimistic_conflict() -> None:
    EXECUTION_OPTIMISTIC_CONFLICTS.inc()


def record_replay_failure(reason: str) -> None:
    if reason not in REPLAY_FAILURE_REASONS:
        raise ValueError(f"unsupported replay failure reason: {reason}")
    EXECUTION_REPLAY_FAILURES.labels(reason=reason).inc()


__all__ = [
    "EXECUTION_ACTIVITY_ROWS",
    "EXECUTION_INBOX_ROWS",
    "EXECUTION_PROJECTOR_CURSOR_LAG",
    "ExecutionMetrics",
    "record_optimistic_conflict",
    "record_replay_failure",
]
