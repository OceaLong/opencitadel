"""Assemble one ExecutionKernelRuntime from its persistence and wake-up ports.

Composition-root construction moved out of ``infrastructure/adapters`` (D14/
P2-13): infrastructure modules provide adapters, but only the composition layer
may know the complete kernel wiring (and import the ``app.execution_kernel``
process module).
"""

from __future__ import annotations

from socket import gethostname

from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError
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
from app.domain.execution.definition import SchemaCompatibility
from app.domain.execution.run import RunAggregate
from app.domain.models.authorization import AuthorizationContext
from app.execution_kernel import ExecutionKernelRuntime
from app.infrastructure.adapters.execution_ports import (
    SqlAlchemyExecutionMetricsAdapter,
    SqlAlchemyOutboxStore,
    SqlAlchemyTimerDispatcher,
)
from app.infrastructure.adapters.redis_capabilities import (
    RedisNotificationPublisher,
    RedisWakeupAdapter,
)
from app.infrastructure.execution.postgres_activity_store import (
    DEFAULT_MAX_CLAIM_ATTEMPTS as DEFAULT_ACTIVITY_MAX_CLAIM_ATTEMPTS,
)
from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
from app.infrastructure.execution.postgres_formal_projector import (
    PostgresApprovalNotifier,
    PostgresFormalProjector,
)
from app.infrastructure.execution.postgres_inbox import (
    DEFAULT_MAX_CLAIM_ATTEMPTS as DEFAULT_INBOX_MAX_CLAIM_ATTEMPTS,
)
from app.infrastructure.execution.postgres_inbox import PostgresInbox
from app.infrastructure.execution.postgres_inbox_source import PostgresInboxSource
from app.infrastructure.execution.postgres_owner_scope_source import PostgresOwnerScopeSource
from app.infrastructure.execution.postgres_progress_sink import PostgresActivityProgressSink
from app.infrastructure.execution.postgres_run_context_source import PostgresRunContextSource
from app.infrastructure.execution.postgres_run_decision_source import PostgresRunDecisionSource
from app.infrastructure.execution.sqlalchemy_orchestrator import (
    SqlAlchemyExecutionOrchestrator,
)
from app.infrastructure.observability.execution_metrics import record_activity_lease_event


def build_execution_kernel_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    authorization: AuthorizationContext,
    activity_registry: ActivityRegistry,
    worker_id: str | None = None,
    activity_max_concurrency: int = DEFAULT_ACTIVITY_MAX_CONCURRENCY,
    activity_max_claim_attempts: int = DEFAULT_ACTIVITY_MAX_CLAIM_ATTEMPTS,
    inbox_max_claim_attempts: int = DEFAULT_INBOX_MAX_CLAIM_ATTEMPTS,
    approval_ttl_minutes=None,
    content_writer=None,
    evaluation_execution=None,
    progress_sink_wrapper=None,
    effect_store=None,
    definitions=None,
) -> ExecutionKernelRuntime:
    from app.infrastructure.execution.postgres_activity_timeout import PostgresActivityTimeoutGuard

    command_handler = SqlAlchemyExecutionOrchestrator(
        activity_timeout=PostgresActivityTimeoutGuard(activity_registry),
        evaluation_execution=evaluation_execution,
        session_factory=session_factory,
        aggregates={"run": RunAggregate()},
        authorization=authorization,
        inbox_factory=lambda session: PostgresInbox(
            session,
            max_claim_attempts=inbox_max_claim_attempts,
        ),
    )
    if effect_store is not None:
        effect_store.bind_orchestrator(command_handler)
    run_service = RunService(orchestrator=command_handler)
    progress_sink = PostgresActivityProgressSink(
        session_factory=session_factory,
        authorization=authorization,
    )
    if progress_sink_wrapper is not None:
        progress_sink = progress_sink_wrapper(progress_sink)
    if definitions is not None:
        definitions.validate_handlers(activity_registry)
    supported_definitions = (
        frozenset(value.digest for value in definitions.manifests)
        if definitions is not None
        else None
    )
    supported_handlers = (
        frozenset(
            (value.digest, item.activity_type, item.handler_version)
            for value in definitions.manifests
            for item in value.handlers
        )
        if definitions is not None
        else None
    )
    supported_schemas = None
    if definitions is not None:
        declared = {
            schema for manifest in definitions.manifests for schema in manifest.command_schemas
        }
        # Cancellation is an aggregate command, independent of planner presence.
        cancel_version = RunAggregate().command_registry.latest_version("CancelRun")
        declared.add(
            SchemaCompatibility(
                name="CancelRun", minimum=1, maximum=cancel_version, write_version=cancel_version
            )
        )
        supported_schemas = tuple(
            sorted(declared, key=lambda schema: (schema.name, schema.minimum, schema.maximum))
        )
    return ExecutionKernelRuntime(
        command_handler=command_handler,
        inbox_worker=InboxWorker(
            source=PostgresInboxSource(
                session_factory=session_factory,
                authorization=authorization,
                supported_schemas=supported_schemas,
            ),
            handler=command_handler,
        ),
        activity_worker=ActivityWorker(
            lease_observer=record_activity_lease_event,
            execution_gate=evaluation_execution,
            content_writer=content_writer,
            store=PostgresActivityStore(
                session_factory=session_factory,
                authorization=authorization,
                max_claim_attempts=activity_max_claim_attempts,
                supported_implementations=supported_handlers,
            ),
            run_contexts=PostgresRunContextSource(
                session_factory=session_factory,
                authorization=authorization,
            ),
            run_service=run_service,
            registry=activity_registry,
            definitions=definitions,
            worker_id=worker_id or f"{gethostname()}:execution-kernel",
            max_concurrency=activity_max_concurrency,
            # The application worker classifies these as transient infrastructure
            # faults (defer + backoff) rather than activity failures; the
            # SQLAlchemy driver family is contributed here at the wiring seam.
            infrastructure_errors=(SQLAlchemyError, OSError, TimeoutError),
            progress_sink=progress_sink,
        ),
        decision_worker=DecisionWorker(
            source=PostgresRunDecisionSource(
                session_factory=session_factory,
                authorization=authorization,
                supported_definitions=supported_definitions,
            ),
            run_service=run_service,
            definitions=definitions,
            approval_ttl_minutes=approval_ttl_minutes,
        ),
        outbox_dispatcher=OutboxDispatcher(
            store=SqlAlchemyOutboxStore(
                session_factory=session_factory,
                authorization=authorization,
            ),
            publisher=RedisWakeupAdapter(redis),
            # Approval notices are outbox rows since K4-2: the projector writes
            # them transactionally, this dispatcher delivers (and redelivers)
            # them through the durable notifier.
            approval_notifier=PostgresApprovalNotifier(
                session_factory=session_factory,
                authorization=authorization,
                publisher=RedisNotificationPublisher(redis),
            ),
            delivery_errors=(OSError, RuntimeError, ValueError, SQLAlchemyError),
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


__all__ = ["build_execution_kernel_runtime"]
