"""SQLAlchemy implementations of formal execution persistence ports."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.execution import (
    OutboxClaim,
    TimerFireResult,
)
from app.application.ports.observability import ExecutionMetricsSnapshot
from app.domain.execution.commands import CommandEnvelope
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
from app.infrastructure.execution.postgres_inbox import PostgresInbox
from app.infrastructure.execution.postgres_outbox import (
    OutboxClaim as PostgresOutboxClaim,
)
from app.infrastructure.execution.postgres_outbox import PostgresOutbox
from app.infrastructure.execution.postgres_timer_store import PostgresTimerStore
from app.infrastructure.observability.execution_metrics import ExecutionMetrics
from app.infrastructure.security.db_authorization import configure_session_authorization


class SqlAlchemyCommandEnvelopeWriter:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext | None,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization

    async def receive(self, command: CommandEnvelope) -> bool:
        async with self._session_factory() as session:
            try:
                await configure_session_authorization(session, self._authorization)
                received = await PostgresInbox(session).receive(command)
                await session.commit()
                return received
            except (OSError, RuntimeError, ValueError, SQLAlchemyError):
                await session.rollback()
                raise


class SqlAlchemyOutboxStore:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization

    async def claim_batch(
        self,
        *,
        limit: int,
        now: datetime,
        claim_ttl: timedelta,
    ) -> tuple[OutboxClaim, ...]:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            claims = await PostgresOutbox(session).claim_batch(
                limit=limit,
                now=now,
                claim_ttl=claim_ttl,
            )
            await session.commit()
        return tuple(
            OutboxClaim(
                outbox_id=claim.outbox_id,
                event_position=claim.event_position,
                destination=claim.destination,
                dedupe_key=claim.dedupe_key,
                generation=claim.generation,
                attempt=claim.attempt,
                payload=claim.payload,
            )
            for claim in claims
        )

    async def mark_delivered(self, claim: OutboxClaim, *, now: datetime) -> bool:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            acknowledged = await PostgresOutbox(session).mark_delivered(
                self._postgres_claim(claim),
                now=now,
            )
            await session.commit()
            return acknowledged

    async def mark_failed(
        self,
        claim: OutboxClaim,
        *,
        now: datetime,
        error_type: str,
        base_retry_delay: timedelta,
        max_retry_delay: timedelta,
    ) -> bool:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            acknowledged = await PostgresOutbox(session).mark_failed(
                self._postgres_claim(claim),
                now=now,
                error_type=error_type,
                base_retry_delay=base_retry_delay,
                max_retry_delay=max_retry_delay,
            )
            await session.commit()
            return acknowledged

    @staticmethod
    def _postgres_claim(claim: OutboxClaim) -> PostgresOutboxClaim:
        return PostgresOutboxClaim(
            outbox_id=claim.outbox_id,
            event_position=claim.event_position,
            destination=claim.destination,
            dedupe_key=claim.dedupe_key,
            generation=claim.generation,
            attempt=claim.attempt,
            payload=claim.payload,
        )


class SqlAlchemyTimerDispatcher:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization

    async def fire_due(
        self,
        *,
        limit: int,
        now: datetime,
        claim_ttl: timedelta,
    ) -> TimerFireResult:
        async with self._session_factory() as session:
            try:
                await configure_session_authorization(session, self._authorization)
                store = PostgresTimerStore(session)
                inbox = PostgresInbox(session)
                claims = await store.claim_due(limit=limit, claim_ttl=claim_ttl)
                fired = failed = 0
                for claim in claims:
                    try:
                        command = CommandEnvelope.model_validate(claim.command_envelope).model_copy(
                            update={
                                "command_id": uuid5(
                                    NAMESPACE_URL,
                                    f"opencitadel:timer:{claim.timer_id}",
                                )
                            }
                        )
                        if (
                            command.owner_user_id != claim.owner_user_id
                            or command.team_id != claim.team_id
                        ):
                            raise ValueError("timer owner scope conflicts with command envelope")
                        await inbox.receive(command)
                        acknowledged = await store.mark_fired(claim, now=now)
                    except (ValidationError, ValueError) as error:
                        acknowledged = await store.mark_dead_lettered(
                            claim,
                            error_type=type(error).__name__,
                        )
                        failed += int(acknowledged)
                        continue
                    fired += int(acknowledged)
                    failed += int(not acknowledged)
                await session.commit()
            except (OSError, RuntimeError, ValueError, SQLAlchemyError):
                await session.rollback()
                raise
        return TimerFireResult(len(claims), fired, failed)


class SqlAlchemyExecutionQueueRetentionStore:
    """Batch deletion over the four durable execution queues (K2-5/D7).

    Each purge runs in its own short transaction so one large queue cannot
    starve the others of the leader tick, and a mid-batch failure loses at
    most one queue's batch.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization

    async def purge_inbox(self, *, before: datetime, limit: int) -> int:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            purged = await PostgresInbox(session).purge_completed(before=before, limit=limit)
            await session.commit()
            return purged

    async def purge_inbox_dead_letters(self, *, before: datetime, limit: int) -> int:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            purged = await PostgresInbox(session).purge_dead_lettered(before=before, limit=limit)
            await session.commit()
            return purged

    async def purge_outbox(self, *, before: datetime, limit: int) -> int:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            purged = await PostgresOutbox(session).purge_completed(before=before, limit=limit)
            await session.commit()
            return purged

    async def purge_timers(self, *, before: datetime, limit: int) -> int:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            purged = await PostgresTimerStore(session).purge_completed(before=before, limit=limit)
            await session.commit()
            return purged

    async def purge_activities(self, *, before: datetime, limit: int) -> int:
        # PostgresActivityStore manages its own session/commit (it enforces the
        # "owning Run must be terminal" purge invariant internally).
        return await PostgresActivityStore(
            session_factory=self._session_factory,
            authorization=self._authorization,
        ).purge_completed(before=before, limit=limit)


class SqlAlchemyExecutionMetricsAdapter:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization
        self._metrics = ExecutionMetrics()

    async def refresh(self, *, now: datetime) -> ExecutionMetricsSnapshot:
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            return await self._metrics.refresh(session, now=now)


__all__ = [
    "SqlAlchemyCommandEnvelopeWriter",
    "SqlAlchemyExecutionMetricsAdapter",
    "SqlAlchemyExecutionQueueRetentionStore",
    "SqlAlchemyOutboxStore",
    "SqlAlchemyTimerDispatcher",
]
