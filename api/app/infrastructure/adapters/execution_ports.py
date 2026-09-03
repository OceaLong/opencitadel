"""SQLAlchemy implementations of formal execution persistence ports."""

from __future__ import annotations

from datetime import datetime, timedelta
from socket import gethostname
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.execution.activity_registry import ActivityRegistry
from app.application.execution.activity_worker import (
    DEFAULT_ACTIVITY_MAX_CONCURRENCY,
    ActivityWorker,
)
from app.application.execution.decision_worker import DecisionWorker
from app.application.execution.inbox_worker import InboxWorker
from app.application.execution.outbox_dispatcher import OutboxDispatcher
from app.application.execution.run_service import RunService
from app.application.execution.timer_dispatcher import TimerDispatcher
from app.application.ports.execution import (
    OutboxClaim,
    TimerFireResult,
)
from app.application.ports.observability import ExecutionMetricsSnapshot
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.run import RunAggregate
from app.domain.models.authorization import AuthorizationContext
from app.execution_kernel import ExecutionKernelRuntime
from app.infrastructure.adapters.redis_capabilities import (
    RedisNotificationPublisher,
    RedisWakeupAdapter,
)
from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
from app.infrastructure.execution.postgres_formal_projector import (
    PostgresApprovalNotifier,
    PostgresFormalProjector,
)
from app.infrastructure.execution.postgres_inbox import PostgresInbox
from app.infrastructure.execution.postgres_inbox_source import PostgresInboxSource
from app.infrastructure.execution.postgres_outbox import (
    OutboxClaim as PostgresOutboxClaim,
)
from app.infrastructure.execution.postgres_outbox import PostgresOutbox
from app.infrastructure.execution.postgres_owner_scope_source import PostgresOwnerScopeSource
from app.infrastructure.execution.postgres_run_context_source import PostgresRunContextSource
from app.infrastructure.execution.postgres_run_decision_source import PostgresRunDecisionSource
from app.infrastructure.execution.postgres_timer_store import PostgresTimerStore
from app.infrastructure.execution.sqlalchemy_orchestrator import (
    SqlAlchemyExecutionOrchestrator,
)
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
            except (OSError, RuntimeError, ValueError):
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
            except (OSError, RuntimeError, ValueError):
                await session.rollback()
                raise
        return TimerFireResult(len(claims), fired, failed)


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


def build_execution_kernel_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    authorization: AuthorizationContext,
    activity_registry: ActivityRegistry,
    worker_id: str | None = None,
    activity_max_concurrency: int = DEFAULT_ACTIVITY_MAX_CONCURRENCY,
    approval_ttl_minutes=None,
) -> ExecutionKernelRuntime:
    command_handler = SqlAlchemyExecutionOrchestrator(
        session_factory=session_factory,
        aggregates={"run": RunAggregate()},
        authorization=authorization,
    )
    run_service = RunService(orchestrator=command_handler)
    return ExecutionKernelRuntime(
        command_handler=command_handler,
        inbox_worker=InboxWorker(
            source=PostgresInboxSource(
                session_factory=session_factory,
                authorization=authorization,
            ),
            handler=command_handler,
        ),
        activity_worker=ActivityWorker(
            store=PostgresActivityStore(
                session_factory=session_factory,
                authorization=authorization,
            ),
            run_contexts=PostgresRunContextSource(
                session_factory=session_factory,
                authorization=authorization,
            ),
            run_service=run_service,
            registry=activity_registry,
            worker_id=worker_id or f"{gethostname()}:execution-kernel",
            max_concurrency=activity_max_concurrency,
        ),
        decision_worker=DecisionWorker(
            source=PostgresRunDecisionSource(
                session_factory=session_factory,
                authorization=authorization,
            ),
            run_service=run_service,
            approval_ttl_minutes=approval_ttl_minutes,
        ),
        outbox_dispatcher=OutboxDispatcher(
            store=SqlAlchemyOutboxStore(
                session_factory=session_factory,
                authorization=authorization,
            ),
            publisher=RedisWakeupAdapter(redis),
        ),
        timer_dispatcher=TimerDispatcher(
            dispatcher=SqlAlchemyTimerDispatcher(
                session_factory=session_factory,
                authorization=authorization,
            )
        ),
        projector=PostgresFormalProjector(
            session_factory=session_factory,
            authorization=authorization,
            notifier=PostgresApprovalNotifier(
                session_factory=session_factory,
                authorization=authorization,
                publisher=RedisNotificationPublisher(redis),
            ),
        ),
        owner_scopes=PostgresOwnerScopeSource(
            session_factory=session_factory,
            authorization=authorization,
        ),
        metrics=SqlAlchemyExecutionMetricsAdapter(
            session_factory=session_factory,
            authorization=authorization,
        ),
        activity_registry=activity_registry,
    )


__all__ = [
    "SqlAlchemyCommandEnvelopeWriter",
    "SqlAlchemyExecutionMetricsAdapter",
    "SqlAlchemyOutboxStore",
    "SqlAlchemyTimerDispatcher",
    "build_execution_kernel_runtime",
]
