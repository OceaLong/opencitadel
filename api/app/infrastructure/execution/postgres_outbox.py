"""PostgreSQL claim and acknowledgement operations for the execution outbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.execution.commands import normalize_utc
from app.infrastructure.execution.models import ExecutionOutboxORM


@dataclass(frozen=True)
class OutboxClaim:
    outbox_id: UUID
    event_position: int
    destination: str
    dedupe_key: str
    generation: int
    attempt: int
    # Destination-specific message body (K4-2); None for plain wakeup hints.
    payload: dict | None = None


class PostgresOutbox:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_batch(
        self,
        *,
        limit: int,
        now: datetime,
        claim_ttl: timedelta,
    ) -> tuple[OutboxClaim, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        resolved_now = normalize_utc(now)
        records = tuple(
            (
                await self._session.scalars(
                    select(ExecutionOutboxORM)
                    .where(
                        ExecutionOutboxORM.delivered_at.is_(None),
                        ExecutionOutboxORM.available_at <= resolved_now,
                        or_(
                            ExecutionOutboxORM.claim_deadline.is_(None),
                            ExecutionOutboxORM.claim_deadline <= resolved_now,
                        ),
                    )
                    .order_by(
                        ExecutionOutboxORM.available_at,
                        ExecutionOutboxORM.created_at,
                        ExecutionOutboxORM.outbox_id,
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        claims: list[OutboxClaim] = []
        for record in records:
            record.attempts += 1
            record.claim_generation += 1
            record.claim_deadline = resolved_now + claim_ttl
            claims.append(
                OutboxClaim(
                    outbox_id=record.outbox_id,
                    event_position=record.event_position,
                    destination=record.destination,
                    dedupe_key=record.dedupe_key,
                    generation=record.claim_generation,
                    attempt=record.attempts,
                    payload=record.payload,
                )
            )
        await self._session.flush()
        return tuple(claims)

    async def mark_delivered(
        self,
        claim: OutboxClaim,
        *,
        now: datetime,
    ) -> bool:
        delivered = await self._session.scalar(
            update(ExecutionOutboxORM)
            .where(
                ExecutionOutboxORM.outbox_id == claim.outbox_id,
                ExecutionOutboxORM.claim_generation == claim.generation,
                ExecutionOutboxORM.delivered_at.is_(None),
            )
            .values(
                delivered_at=normalize_utc(now),
                claim_deadline=None,
                last_error=None,
            )
            .returning(ExecutionOutboxORM.outbox_id)
        )
        return delivered is not None

    async def mark_failed(
        self,
        claim: OutboxClaim,
        *,
        now: datetime,
        error_type: str,
        base_retry_delay: timedelta,
        max_retry_delay: timedelta,
    ) -> bool:
        if base_retry_delay <= timedelta(0):
            raise ValueError("base_retry_delay must be positive")
        if max_retry_delay < base_retry_delay:
            raise ValueError("max_retry_delay must not be smaller than base delay")
        resolved_now = normalize_utc(now)
        multiplier = 2 ** min(claim.attempt - 1, 30)
        retry_delay = min(base_retry_delay * multiplier, max_retry_delay)
        failed = await self._session.scalar(
            update(ExecutionOutboxORM)
            .where(
                ExecutionOutboxORM.outbox_id == claim.outbox_id,
                ExecutionOutboxORM.claim_generation == claim.generation,
                ExecutionOutboxORM.delivered_at.is_(None),
            )
            .values(
                available_at=resolved_now + retry_delay,
                claim_deadline=None,
                last_error=error_type[:255],
            )
            .returning(ExecutionOutboxORM.outbox_id)
        )
        return failed is not None

    async def purge_completed(self, *, before: datetime, limit: int) -> int:
        """Delete a batch of delivered outbox rows older than ``before``."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        resolved_before = normalize_utc(before)
        purgeable = (
            select(ExecutionOutboxORM.outbox_id)
            .where(
                ExecutionOutboxORM.delivered_at.is_not(None),
                ExecutionOutboxORM.delivered_at < resolved_before,
            )
            .limit(limit)
        )
        result = await self._session.execute(
            delete(ExecutionOutboxORM).where(ExecutionOutboxORM.outbox_id.in_(purgeable))
        )
        return int(result.rowcount or 0)


__all__ = ["OutboxClaim", "PostgresOutbox"]
