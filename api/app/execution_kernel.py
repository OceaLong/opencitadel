"""Application-only orchestration for one execution-kernel process."""

from __future__ import annotations

import asyncio
import logging
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
from app.domain.models.scope import OwnerScope, OwnerScopeType

logger = logging.getLogger(__name__)

# Consecutive per-scope projection failures before the scope is durably
# quarantined (D12/K4-1). Counted in process memory: a kernel restart resets
# the streak, which only delays quarantine by a few extra attempts.
_SCOPE_FAILURE_QUARANTINE_THRESHOLD = 3


def _scope_key(owner_scope: OwnerScope) -> str:
    if owner_scope.type == OwnerScopeType.TEAM and owner_scope.team_id:
        return f"team:{owner_scope.team_id}"
    return f"user:{owner_scope.user_id}"


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
        # In-memory consecutive projection-failure streaks keyed by owner-scope
        # key (K4-1). A successful pass clears the streak; reaching the
        # threshold quarantines the scope durably via the owner-scope source.
        self._scope_failures: dict[str, int] = {}

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
            # Per-scope failure isolation (D12/K4-1): one corrupt scope must
            # never abort the pass and starve every other scope. Failures are
            # counted per scope; a streak of them quarantines the scope so
            # ``list_pending`` stops offering it until an operator rebuilds it.
            try:
                result = await self._projector.run_once(owner_scope, limit=event_limit)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - scope isolation boundary
                await self._record_scope_failure(owner_scope, error)
                continue
            if not result.busy:
                self._scope_failures.pop(_scope_key(owner_scope), None)
            processed += result.processed
            last_position = max(last_position, result.last_position)
        return FormalProjectorResult(processed=processed, last_position=last_position)

    async def _record_scope_failure(self, owner_scope: OwnerScope, error: Exception) -> None:
        key = _scope_key(owner_scope)
        count = self._scope_failures.get(key, 0) + 1
        self._scope_failures[key] = count
        logger.error(
            "formal projection failed for scope %s (consecutive failure %d/%d): %s",
            key,
            count,
            _SCOPE_FAILURE_QUARANTINE_THRESHOLD,
            error,
            exc_info=error,
        )
        if count < _SCOPE_FAILURE_QUARANTINE_THRESHOLD:
            return
        try:
            await self._owner_scopes.quarantine(
                owner_scope,
                reason=type(error).__name__,
                error=str(error),
                failure_count=count,
            )
        except Exception:
            # The quarantine write itself failed (e.g. the store is down); keep
            # the in-memory streak so the next pass retries the quarantine.
            logger.exception("failed to quarantine poisoned scope %s", key)
            return
        self._scope_failures.pop(key, None)
        logger.error(
            "quarantined poisoned projection scope %s after %d consecutive failures",
            key,
            count,
        )

    async def refresh_metrics(
        self,
        *,
        now: datetime | None = None,
    ) -> ExecutionMetricsSnapshot:
        return await self._metrics.refresh(now=now or datetime.now(UTC))


__all__ = ["ExecutionKernelRuntime"]
