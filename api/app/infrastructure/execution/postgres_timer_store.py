"""PostgreSQL timer claims for durable scheduled Commands."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.execution.commands import normalize_utc
from app.domain.execution.events import StoredEvent
from app.infrastructure.execution.models import ExecutionScheduledCommandORM


@dataclass(frozen=True)
class TimerClaim:
    timer_id: UUID
    command_envelope: dict[str, Any]
    owner_user_id: str | None
    team_id: str | None
    generation: int


class PostgresTimerStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_due(
        self,
        *,
        limit: int,
        claim_ttl: timedelta,
    ) -> tuple[TimerClaim, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        database_now = await self._session.scalar(select(func.current_timestamp()))
        if database_now is None:
            raise RuntimeError("database did not provide a current timestamp")
        resolved_now = normalize_utc(database_now)
        records = tuple(
            (
                await self._session.scalars(
                    select(ExecutionScheduledCommandORM)
                    .where(
                        ExecutionScheduledCommandORM.status == "pending",
                        ExecutionScheduledCommandORM.cancellation_event_id.is_(None),
                        ExecutionScheduledCommandORM.due_at <= resolved_now,
                        or_(
                            ExecutionScheduledCommandORM.claim_deadline.is_(None),
                            ExecutionScheduledCommandORM.claim_deadline <= resolved_now,
                        ),
                    )
                    .order_by(
                        ExecutionScheduledCommandORM.due_at,
                        ExecutionScheduledCommandORM.created_at,
                        ExecutionScheduledCommandORM.timer_id,
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        claims: list[TimerClaim] = []
        for record in records:
            record.claimed_generation += 1
            record.attempts += 1
            record.claim_deadline = resolved_now + claim_ttl
            claims.append(
                TimerClaim(
                    timer_id=record.timer_id,
                    command_envelope=dict(record.command_envelope),
                    owner_user_id=record.owner_user_id,
                    team_id=record.team_id,
                    generation=record.claimed_generation,
                )
            )
        await self._session.flush()
        return tuple(claims)

    async def cancel_matching(
        self,
        events: Sequence[StoredEvent],
    ) -> int:
        cancelled = 0
        for event in events:
            scope_filters = (
                ExecutionScheduledCommandORM.owner_user_id.is_(None)
                if event.owner_user_id is None
                else ExecutionScheduledCommandORM.owner_user_id == event.owner_user_id,
                ExecutionScheduledCommandORM.team_id.is_(None)
                if event.team_id is None
                else ExecutionScheduledCommandORM.team_id == event.team_id,
            )
            activity_filter = True
            if event.event_type.startswith("Activity"):
                activity_id = event.public_payload.get("activity_id")
                if activity_id is None:
                    continue
                activity_filter = or_(
                    ExecutionScheduledCommandORM.cancellation_activity_id.is_(None),
                    ExecutionScheduledCommandORM.cancellation_activity_id == UUID(str(activity_id)),
                )
            result = await self._session.execute(
                update(ExecutionScheduledCommandORM)
                .where(
                    ExecutionScheduledCommandORM.status == "pending",
                    ExecutionScheduledCommandORM.cancellation_event_id.is_(None),
                    ExecutionScheduledCommandORM.cancellation_event_types.contains(
                        [event.event_type]
                    ),
                    ExecutionScheduledCommandORM.command_envelope["stream_type"].astext
                    == event.stream_type,
                    ExecutionScheduledCommandORM.command_envelope["stream_id"].astext
                    == event.stream_id,
                    activity_filter,
                    *scope_filters,
                )
                .values(
                    status="cancelled",
                    cancellation_event_id=event.event_id,
                    claim_deadline=None,
                    last_error=None,
                )
            )
            cancelled += result.rowcount or 0
        return cancelled

    async def mark_fired(
        self,
        claim: TimerClaim,
        *,
        now: datetime,
    ) -> bool:
        fired = await self._session.scalar(
            update(ExecutionScheduledCommandORM)
            .where(
                ExecutionScheduledCommandORM.timer_id == claim.timer_id,
                ExecutionScheduledCommandORM.claimed_generation == claim.generation,
                ExecutionScheduledCommandORM.status == "pending",
                ExecutionScheduledCommandORM.cancellation_event_id.is_(None),
            )
            .values(
                status="fired",
                fired_at=normalize_utc(now),
                claim_deadline=None,
                last_error=None,
            )
            .returning(ExecutionScheduledCommandORM.timer_id)
        )
        return fired is not None

    async def mark_dead_lettered(
        self,
        claim: TimerClaim,
        *,
        error_type: str,
    ) -> bool:
        dead_lettered = await self._session.scalar(
            update(ExecutionScheduledCommandORM)
            .where(
                ExecutionScheduledCommandORM.timer_id == claim.timer_id,
                ExecutionScheduledCommandORM.claimed_generation == claim.generation,
                ExecutionScheduledCommandORM.status == "pending",
            )
            .values(
                status="dead_lettered",
                claim_deadline=None,
                last_error=error_type[:255],
            )
            .returning(ExecutionScheduledCommandORM.timer_id)
        )
        return dead_lettered is not None

    async def purge_completed(self, *, before: datetime, limit: int) -> int:
        """Delete a batch of settled timers (fired/cancelled/dead_lettered)."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        resolved_before = normalize_utc(before)
        purgeable = (
            select(ExecutionScheduledCommandORM.timer_id)
            .where(
                ExecutionScheduledCommandORM.status.in_(("fired", "cancelled", "dead_lettered")),
                ExecutionScheduledCommandORM.due_at < resolved_before,
            )
            .limit(limit)
        )
        result = await self._session.execute(
            delete(ExecutionScheduledCommandORM).where(
                ExecutionScheduledCommandORM.timer_id.in_(purgeable)
            )
        )
        return int(result.rowcount or 0)


__all__ = ["PostgresTimerStore", "TimerClaim"]
