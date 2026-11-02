"""Low-cardinality operational metrics for the sole execution kernel."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.observability import ExecutionMetricsSnapshot
from app.domain.execution.commands import normalize_utc
from app.infrastructure.execution.effect_models import operations, receipts, requests
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
    "snapshot_schema_drift",
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
EFFECT_STATES = ("prepared", "sending", "pending", "unknown", "applied", "not_applied")
EFFECT_REQUEST_KINDS = (
    "query",
    "auto_query",
    "resolution",
    "abandon",
    "provider_observation",
    "reconcile_fact",
    "local_not_sent",
)
EFFECT_REQUEST_STATUSES = ("pending", "running", "completed", "failed", "paused")
EFFECT_OBSERVATION_STATES = ("pending", "unknown", "applied", "not_applied")
EXECUTION_EFFECT_ROWS = Gauge(
    "execution_effect_rows", "Retained external operations by state", ("state",)
)
EXECUTION_EFFECT_OLDEST_AGE_SECONDS = Gauge(
    "execution_effect_oldest_age_seconds",
    "Age since creation of the oldest retained operation in each state",
    ("state",),
)
EXECUTION_EFFECT_REQUEST_ROWS = Gauge(
    "execution_effect_request_rows",
    "Durable verification requests by kind and status",
    ("kind", "status"),
)
EXECUTION_EFFECT_QUERY_OBSERVATIONS = Gauge(
    "execution_effect_query_observation_rows", "Retained provider query observations", ("state",)
)
EXECUTION_EFFECT_MANUAL_RESOLUTIONS = Gauge(
    "execution_effect_manual_resolution_rows",
    "Retained evidence-backed human resolutions",
    ("state",),
)
EXECUTION_EFFECT_RESOLUTION_UNAVAILABLE_ROWS = Gauge(
    "execution_effect_resolution_unavailable_rows",
    "Verification requests failed because infrastructure was unavailable",
)
EXECUTION_EFFECT_INTEGRITY_CONFLICT_ROWS = Gauge(
    "execution_effect_integrity_conflict_rows",
    "Retained external operations quarantined by contradictory receipts",
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
ACTIVITY_LEASE_EVENTS = frozenset(
    {
        "heartbeat_error",
        "heartbeat_rejected",
        "heartbeat_recovered",
        "lease_lost",
        "cancellation_timeout",
        "settlement_rejected",
    }
)
EXECUTION_ACTIVITY_LEASE_EVENTS = Counter(
    "execution_activity_lease_events_total",
    "Activity ownership failures and recoveries without per-activity labels",
    ("event",),
)
EXECUTION_REPLAY_FAILURES = Counter(
    "execution_replay_failures_total",
    "Replay, hash-chain and projection integrity failures",
    ("reason",),
)
EXECUTION_POISONED_RUNS = Counter(
    "execution_poisoned_runs_total",
    "Run projections quarantined for failing to decode during decisions",
)
EXECUTION_POISONED_SCOPES = Counter(
    "execution_poisoned_scopes_total",
    "Owner scopes quarantined after consecutive formal-projection failures",
)
# Control-plane lane latency (D13/K4-3): one histogram per lane pass.
EXECUTION_LANE_DURATION_SECONDS = Histogram(
    "execution_lane_duration_seconds",
    "Duration of one control-plane lane pass",
    ("lane",),
    buckets=(0.005, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
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

        effects = await self._effect_metrics(session, now=resolved_now)
        return ExecutionMetricsSnapshot(
            inbox_rows=inbox_rows,
            inbox_oldest_age_seconds=inbox_ages,
            outbox_lag_seconds=outbox_lag,
            outbox_redeliveries=outbox_redeliveries,
            timer_lag_seconds=timer_lag,
            activity_rows=activity_rows,
            activity_oldest_age_seconds=activity_ages,
            projector_cursor_lag=projector_lag,
            **effects,
        )

    @classmethod
    async def _effect_metrics(cls, session: AsyncSession, *, now: datetime) -> dict:
        state = operations.c.body["state"].astext
        rows = dict.fromkeys(EFFECT_STATES, 0)
        ages = dict.fromkeys(EFFECT_STATES, 0.0)
        for status, count, oldest in (
            await session.execute(
                select(state, func.count(), func.min(operations.c.created_at)).group_by(state)
            )
        ).all():
            if status in rows:
                rows[status] = int(count)
                ages[status] = cls._age(now, oldest)
        for status in EFFECT_STATES:
            EXECUTION_EFFECT_ROWS.labels(state=status).set(rows[status])
            EXECUTION_EFFECT_OLDEST_AGE_SECONDS.labels(state=status).set(ages[status])
        request_rows = {
            f"{kind}:{status}": 0
            for kind in EFFECT_REQUEST_KINDS
            for status in EFFECT_REQUEST_STATUSES
        }
        for kind, status, count in (
            await session.execute(
                select(requests.c.kind, requests.c.status, func.count()).group_by(
                    requests.c.kind, requests.c.status
                )
            )
        ).all():
            key = f"{kind}:{status}"
            if key in request_rows:
                request_rows[key] = int(count)
        for kind in EFFECT_REQUEST_KINDS:
            for status in EFFECT_REQUEST_STATUSES:
                EXECUTION_EFFECT_REQUEST_ROWS.labels(kind=kind, status=status).set(
                    request_rows[f"{kind}:{status}"]
                )
        observations = {
            kind: dict.fromkeys(EFFECT_OBSERVATION_STATES, 0) for kind in ("query", "resolution")
        }
        observation_state = receipts.c.body["state"].astext
        for kind, status, count in (
            await session.execute(
                select(requests.c.kind, observation_state, func.count())
                .select_from(
                    receipts.join(requests, receipts.c.receipt_id == requests.c.request_id)
                )
                .where(requests.c.kind.in_(("query", "auto_query", "resolution")))
                .group_by(requests.c.kind, observation_state)
            )
        ).all():
            destination = "query" if kind == "auto_query" else kind
            if status in observations[destination]:
                observations[destination][status] += int(count)
        for status in EFFECT_OBSERVATION_STATES:
            EXECUTION_EFFECT_QUERY_OBSERVATIONS.labels(state=status).set(
                observations["query"][status]
            )
            EXECUTION_EFFECT_MANUAL_RESOLUTIONS.labels(state=status).set(
                observations["resolution"][status]
            )
        unavailable = int(
            await session.scalar(
                select(func.count())
                .select_from(requests)
                .where(
                    requests.c.status == "failed",
                    requests.c.error_code == "EFFECT_RESOLUTION_UNAVAILABLE",
                )
            )
            or 0
        )
        EXECUTION_EFFECT_RESOLUTION_UNAVAILABLE_ROWS.set(unavailable)
        conflicts = int(
            await session.scalar(
                select(func.count())
                .select_from(operations)
                .where(operations.c.body["integrity_conflict"].astext == "true")
            )
            or 0
        )
        EXECUTION_EFFECT_INTEGRITY_CONFLICT_ROWS.set(conflicts)
        return {
            "effect_rows": rows,
            "effect_oldest_age_seconds": ages,
            "effect_request_rows": request_rows,
            "effect_query_observations": observations["query"],
            "effect_manual_resolutions": observations["resolution"],
            "effect_resolution_unavailable_rows": unavailable,
            "effect_integrity_conflict_rows": conflicts,
        }

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


def record_activity_lease_event(event: str) -> None:
    if event not in ACTIVITY_LEASE_EVENTS:
        raise ValueError("unsupported activity lease event")
    EXECUTION_ACTIVITY_LEASE_EVENTS.labels(event=event).inc()


def record_replay_failure(reason: str) -> None:
    if reason not in REPLAY_FAILURE_REASONS:
        raise ValueError(f"unsupported replay failure reason: {reason}")
    EXECUTION_REPLAY_FAILURES.labels(reason=reason).inc()


def record_poisoned_run() -> None:
    EXECUTION_POISONED_RUNS.inc()


def record_poisoned_scope() -> None:
    EXECUTION_POISONED_SCOPES.inc()


def observe_lane_duration(lane: str, seconds: float) -> None:
    EXECUTION_LANE_DURATION_SECONDS.labels(lane=lane).observe(seconds)


__all__ = [
    "EXECUTION_ACTIVITY_LEASE_EVENTS",
    "EXECUTION_ACTIVITY_ROWS",
    "EXECUTION_INBOX_ROWS",
    "EXECUTION_LANE_DURATION_SECONDS",
    "EXECUTION_POISONED_RUNS",
    "EXECUTION_POISONED_SCOPES",
    "EXECUTION_PROJECTOR_CURSOR_LAG",
    "ExecutionMetrics",
    "observe_lane_duration",
    "record_activity_lease_event",
    "record_optimistic_conflict",
    "record_poisoned_run",
    "record_poisoned_scope",
    "record_replay_failure",
]
