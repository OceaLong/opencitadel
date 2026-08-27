"""Application-only orchestration for one execution-kernel process."""

from __future__ import annotations

from datetime import UTC, datetime

from app.application.execution.activity_registry import ActivityRegistry
from app.application.execution.activity_worker import ActivityBatchStats, ActivityWorker
from app.application.execution.decision_worker import DecisionBatchStats, DecisionWorker
from app.application.execution.inbox_worker import InboxBatchStats, InboxWorker
from app.application.execution.outbox_dispatcher import DispatchStats, OutboxDispatcher
from app.application.execution.timer_dispatcher import TimerDispatcher, TimerDispatchStats
from app.application.ports.execution import (
    CommandResult,
    ExecutionCommandHandlerPort,
    FormalProjectorPort,
    FormalProjectorResult,
    OwnerScopeSourcePort,
)
from app.application.ports.observability import ExecutionMetricsPort, ExecutionMetricsSnapshot
from app.domain.execution.commands import CommandEnvelope
from app.domain.models.scope import OwnerScope


class ExecutionKernelRuntime:
    """Coordinate durable workers without knowing their persistence technology."""

    def __init__(
        self,
        *,
        command_handler: ExecutionCommandHandlerPort,
        inbox_worker: InboxWorker,
        activity_worker: ActivityWorker,
        decision_worker: DecisionWorker,
        outbox_dispatcher: OutboxDispatcher,
        timer_dispatcher: TimerDispatcher,
        projector: FormalProjectorPort,
        owner_scopes: OwnerScopeSourcePort,
        metrics: ExecutionMetricsPort,
        activity_registry: ActivityRegistry,
    ) -> None:
        self._command_handler = command_handler
        self._inbox = inbox_worker
        self._activities = activity_worker
        self._decisions = decision_worker
        self._outbox = outbox_dispatcher
        self._timers = timer_dispatcher
        self._projector = projector
        self._owner_scopes = owner_scopes
        self._metrics = metrics
        self.activity_registry = activity_registry

    async def handle(self, command: CommandEnvelope) -> CommandResult:
        return await self._command_handler.handle(command)

    async def run_inbox_once(
        self,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> InboxBatchStats:
        return await self._inbox.run_once(limit=limit, now=now or datetime.now(UTC))

    async def run_outbox_once(
        self,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> DispatchStats:
        return await self._outbox.dispatch_batch(limit=limit, now=now or datetime.now(UTC))

    async def run_activities_once(
        self,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> ActivityBatchStats:
        return await self._activities.run_once(limit=limit, now=now or datetime.now(UTC))

    async def run_decisions_once(
        self,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> DecisionBatchStats:
        return await self._decisions.run_once(limit=limit, now=now or datetime.now(UTC))

    async def run_timers_once(
        self,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> TimerDispatchStats:
        return await self._timers.fire_due(limit=limit, now=now or datetime.now(UTC))

    async def run_projector_once(
        self,
        owner_scope: OwnerScope,
        *,
        limit: int = 1000,
        rebuild: bool = False,
        through_position: int | None = None,
    ) -> FormalProjectorResult:
        if rebuild:
            return await self._projector.rebuild(
                owner_scope,
                through_position=through_position,
                batch_size=limit,
            )
        return await self._projector.run_once(
            owner_scope,
            limit=limit,
            through_position=through_position,
        )

    async def run_pending_projectors_once(
        self,
        *,
        scope_limit: int = 100,
        event_limit: int = 1000,
    ) -> FormalProjectorResult:
        scopes = await self._owner_scopes.list_pending(limit=scope_limit)
        processed = last_position = 0
        for owner_scope in scopes:
            result = await self._projector.run_once(owner_scope, limit=event_limit)
            processed += result.processed
            last_position = max(last_position, result.last_position)
        return FormalProjectorResult(processed=processed, last_position=last_position)

    async def refresh_metrics(
        self,
        *,
        now: datetime | None = None,
    ) -> ExecutionMetricsSnapshot:
        return await self._metrics.refresh(now=now or datetime.now(UTC))


__all__ = ["ExecutionKernelRuntime"]
