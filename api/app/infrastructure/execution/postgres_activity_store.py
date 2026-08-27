"""PostgreSQL lease fencing for durable execution Activities."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.execution.activity import (
    ActivityClaim,
    ActivityRequest,
)
from app.domain.execution.commands import normalize_utc
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import ExecutionActivityTaskORM
from app.infrastructure.security.db_authorization import configure_session_authorization


class PostgresActivityStore:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization

    async def claim(
        self,
        *,
        now: datetime,
        limit: int,
        worker_id: str,
        claim_ttl: timedelta,
    ) -> tuple[ActivityClaim, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        resolved_now = normalize_utc(now)
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            records = tuple(
                (
                    await session.scalars(
                        select(ExecutionActivityTaskORM)
                        .where(
                            ExecutionActivityTaskORM.available_at <= resolved_now,
                            or_(
                                ExecutionActivityTaskORM.status == "pending",
                                and_(
                                    ExecutionActivityTaskORM.status.in_(
                                        ("claimed", "call_started")
                                    ),
                                    ExecutionActivityTaskORM.claim_deadline <= resolved_now,
                                ),
                            ),
                        )
                        .order_by(
                            ExecutionActivityTaskORM.available_at,
                            ExecutionActivityTaskORM.created_at,
                            ExecutionActivityTaskORM.activity_id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            claims: list[ActivityClaim] = []
            for record in records:
                recovered_after_call_started = record.call_started_at is not None
                record.status = "claimed"
                record.claim_generation += 1
                record.claimed_by = worker_id
                record.claim_deadline = resolved_now + claim_ttl
                record.heartbeat_at = resolved_now
                record.updated_at = resolved_now
                claims.append(
                    ActivityClaim(
                        request=self._request(record),
                        claim_generation=record.claim_generation,
                        owner_user_id=record.owner_user_id,
                        team_id=record.team_id,
                        recovered_after_call_started=recovered_after_call_started,
                    )
                )
            await session.commit()
            return tuple(claims)

    async def mark_call_started(
        self,
        claim: ActivityClaim,
        *,
        now: datetime,
    ) -> bool:
        resolved_now = normalize_utc(now)
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            started = await session.scalar(
                update(ExecutionActivityTaskORM)
                .where(
                    ExecutionActivityTaskORM.activity_id == claim.request.activity_id,
                    ExecutionActivityTaskORM.claim_generation == claim.claim_generation,
                    ExecutionActivityTaskORM.status == "claimed",
                )
                .values(
                    status="call_started",
                    attempt=ExecutionActivityTaskORM.attempt + 1,
                    call_started_at=resolved_now,
                    heartbeat_at=resolved_now,
                    updated_at=resolved_now,
                )
                .returning(ExecutionActivityTaskORM.activity_id)
            )
            await session.commit()
            return started is not None

    async def heartbeat(
        self,
        claim: ActivityClaim,
        *,
        now: datetime,
        claim_ttl: timedelta,
    ) -> bool:
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        resolved_now = normalize_utc(now)
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            heartbeat = await session.scalar(
                update(ExecutionActivityTaskORM)
                .where(
                    ExecutionActivityTaskORM.activity_id == claim.request.activity_id,
                    ExecutionActivityTaskORM.claim_generation == claim.claim_generation,
                    ExecutionActivityTaskORM.status.in_(("claimed", "call_started")),
                )
                .values(
                    heartbeat_at=resolved_now,
                    claim_deadline=resolved_now + claim_ttl,
                    updated_at=resolved_now,
                )
                .returning(ExecutionActivityTaskORM.activity_id)
            )
            await session.commit()
            return heartbeat is not None

    async def defer(
        self,
        claim: ActivityClaim,
        *,
        now: datetime,
        retry_after: timedelta,
    ) -> bool:
        if retry_after <= timedelta(0):
            raise ValueError("retry_after must be positive")
        resolved_now = normalize_utc(now)
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            deferred = await session.scalar(
                update(ExecutionActivityTaskORM)
                .where(
                    ExecutionActivityTaskORM.activity_id == claim.request.activity_id,
                    ExecutionActivityTaskORM.claim_generation == claim.claim_generation,
                    ExecutionActivityTaskORM.status.in_(("claimed", "call_started")),
                )
                .values(
                    status="pending",
                    available_at=resolved_now + retry_after,
                    claimed_by=None,
                    claim_deadline=None,
                    heartbeat_at=None,
                    updated_at=resolved_now,
                )
                .returning(ExecutionActivityTaskORM.activity_id)
            )
            await session.commit()
            return deferred is not None

    @staticmethod
    def _request(record: ExecutionActivityTaskORM) -> ActivityRequest:
        return ActivityRequest(
            activity_id=record.activity_id,
            activity_type=record.activity_type,
            aggregate_type=record.aggregate_type,
            aggregate_id=record.aggregate_id,
            generation=record.request_generation,
            timeout_at=record.timeout_at,
            input_ref=record.request_ref,
            input_digest=record.request_digest,
            input_payload=record.request_payload,
        )


__all__ = ["PostgresActivityStore"]
