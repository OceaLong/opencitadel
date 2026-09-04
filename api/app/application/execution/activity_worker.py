"""Lease-fenced worker for durable external Activities."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict

from app.application.execution.activity_registry import (
    ActivityRegistry,
    UnknownActivityTypeError,
)
from app.application.execution.orchestrator import CommandResult
from app.application.execution.progress import ActivityProgressRecord, ActivityProgressSink
from app.application.execution.run_context import RunContextSource, RunContextUnavailableError
from app.domain.execution.activity import (
    ActivityClaim,
    ActivityContext,
    ActivityOutcome,
)
from app.domain.execution.commands import CommandContext, RegisteredCommand
from app.domain.execution.context import RunExecutionContext
from app.domain.models.scope import OwnerScopeType
from app.domain.runtime_policy.errors import RuntimePolicyIntegrityError
from core.otel import get_tracer

logger = logging.getLogger(__name__)
_tracer = get_tracer("opencitadel.execution.activity")

# Exception types treated as transient worker infrastructure faults (K2-2/D5):
# a claim hitting one of these is deferred with backoff instead of settling the
# activity as failed. The persistence adapter extends this tuple with its own
# driver exceptions (e.g. SQLAlchemy's) at wiring time — the application layer
# deliberately does not import persistence libraries.
DEFAULT_INFRASTRUCTURE_ERRORS: tuple[type[Exception], ...] = (OSError, TimeoutError)

# Default per-process concurrency ceiling for executing claims (P1-3 backpressure).
# Each ``_execute_claim`` checks out several DB connections (run-context load,
# mark-call-started, outcome submit, heartbeats), so unbounded ``gather`` over a
# 100-row batch stampedes the connection pool (pool_size + overflow == 10 by
# default) and creates a deadlock vector. Callers should pass
# ``settings.execution_activity_max_concurrency`` (kept <= pool capacity), as
# ``build_execution_kernel_runtime`` does.
DEFAULT_ACTIVITY_MAX_CONCURRENCY = 8


class ActivityStore(Protocol):
    async def claim(
        self,
        *,
        now: datetime,
        limit: int,
        worker_id: str,
        claim_ttl: timedelta,
    ) -> tuple[ActivityClaim, ...]: ...

    async def mark_call_started(
        self,
        claim: ActivityClaim,
        *,
        now: datetime,
    ) -> bool: ...

    async def heartbeat(
        self,
        claim: ActivityClaim,
        *,
        now: datetime,
        claim_ttl: timedelta,
    ) -> bool: ...

    async def defer(
        self,
        claim: ActivityClaim,
        *,
        now: datetime,
        retry_after: timedelta,
    ) -> bool: ...


class RunCommandService(Protocol):
    async def submit(
        self,
        command: RegisteredCommand,
        context: CommandContext,
    ) -> CommandResult: ...


class ActivityBatchStats(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    claimed: int = 0
    succeeded: int = 0
    failed: int = 0
    unknown: int = 0
    stale: int = 0
    deferred: int = 0


class ActivityWorker:
    def __init__(
        self,
        *,
        store: ActivityStore,
        run_contexts: RunContextSource,
        run_service: RunCommandService,
        registry: ActivityRegistry,
        worker_id: str,
        claim_ttl: timedelta = timedelta(seconds=30),
        max_concurrency: int = DEFAULT_ACTIVITY_MAX_CONCURRENCY,
        progress_sink: ActivityProgressSink | None = None,
        infrastructure_errors: tuple[type[Exception], ...] = DEFAULT_INFRASTRUCTURE_ERRORS,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        self._store = store
        self._run_contexts = run_contexts
        self._run_service = run_service
        self._registry = registry
        self._worker_id = worker_id
        self._claim_ttl = claim_ttl
        self._max_concurrency = max_concurrency
        self._progress_sink = progress_sink
        self._infrastructure_errors = infrastructure_errors
        # Bounds concurrent claim execution (and therefore concurrent connection
        # demand) regardless of the claim batch size. See module docstring above.
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def run_once(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> ActivityBatchStats:
        if limit <= 0:
            raise ValueError("limit must be positive")
        claims = await self._store.claim(
            now=now,
            limit=limit,
            worker_id=self._worker_id,
            claim_ttl=self._claim_ttl,
        )
        counts = {
            "claimed": len(claims),
            "succeeded": 0,
            "failed": 0,
            "unknown": 0,
            "stale": 0,
            "deferred": 0,
        }
        # Worker resilience (D5): one claim raising must never abort its batch
        # siblings or tear down the activity lane. Exceptions are collected per
        # claim and classified below instead of propagating out of the batch.
        statuses = await asyncio.gather(
            *(self._execute_claim(claim, now=now) for claim in claims),
            return_exceptions=True,
        )
        for claim, status in zip(claims, statuses, strict=True):
            if isinstance(status, BaseException):
                counts[await self._absorb_claim_failure(claim, status)] += 1
                continue
            counts[status] += 1
        return ActivityBatchStats(**counts)

    async def _absorb_claim_failure(self, claim: ActivityClaim, error: BaseException) -> str:
        """Classify one claim's escaped exception; never re-raise (except cancel).

        Infrastructure faults (database, network, timeouts) and unknown bugs
        both defer the claim with a claim-generation backoff: the lease-fenced
        row becomes claimable again later, and the store's claim-attempt cap
        eventually dead-letters a genuine poison pill.
        """
        if isinstance(error, asyncio.CancelledError):
            # Process shutdown cancellation must keep propagating.
            raise error
        if isinstance(error, RunContextUnavailableError):
            # The scope's formal projection is being rebuilt (K4-1): the Run
            # context row will reappear once the rebuild completes, so this is
            # a transient wait, not a policy failure.
            logger.warning(
                "Activity claim deferred: Run context unavailable during projection "
                "rebuild activity_id=%s activity_type=%s",
                claim.request.activity_id,
                claim.request.activity_type,
            )
        elif isinstance(error, self._infrastructure_errors):
            logger.warning(
                "Activity claim infrastructure failure activity_id=%s activity_type=%s: %s",
                claim.request.activity_id,
                claim.request.activity_type,
                error,
            )
        else:
            logger.error(
                "Activity claim unexpected failure activity_id=%s activity_type=%s",
                claim.request.activity_id,
                claim.request.activity_type,
                exc_info=error,
            )
        retry_after = timedelta(seconds=min(5.0 * max(1, claim.claim_generation), 60.0))
        try:
            deferred = await self._store.defer(
                claim,
                now=datetime.now(UTC),
                retry_after=retry_after,
            )
        except Exception as defer_error:  # noqa: BLE001 - absorbing boundary
            # The defer itself failed (e.g. database still down). The claim
            # lease will expire and the row becomes claimable again anyway.
            logger.warning(
                "Activity claim defer failed activity_id=%s: %s",
                claim.request.activity_id,
                defer_error,
            )
            return "stale"
        return "deferred" if deferred else "stale"

    async def _execute_claim(
        self,
        claim: ActivityClaim,
        *,
        now: datetime,
    ) -> str:
        # Backpressure: cap the number of claims executing at once (P1-3). The
        # whole batch is still ``gather``-ed, but only ``max_concurrency`` claims
        # hold their DB connections at any moment. The tracing span and status
        # accounting are unchanged inside the guard.
        async with self._semaphore:
            return await self._execute_claim_span(claim, now=now)

    async def _execute_claim_span(
        self,
        claim: ActivityClaim,
        *,
        now: datetime,
    ) -> str:
        with _tracer.start_as_current_span("activity.execute") as span:
            span.set_attribute("opencitadel.run_id", claim.request.aggregate_id)
            span.set_attribute("opencitadel.activity_id", str(claim.request.activity_id))
            span.set_attribute("opencitadel.activity_type", claim.request.activity_type)
            span.set_attribute("opencitadel.worker_id", self._worker_id)
            status = await self._execute_claim_traced(claim, now=now)
            span.set_attribute("opencitadel.activity_status", status)
            return status

    async def _execute_claim_traced(
        self,
        claim: ActivityClaim,
        *,
        now: datetime,
    ) -> str:
        claim_started = time.monotonic()
        try:
            run_context = await self._run_contexts.load(UUID(claim.request.aggregate_id))
            self._validate_claim_context(claim, run_context)
        except (RuntimePolicyIntegrityError, ValueError):
            outcome = ActivityOutcome.failed(failure_code="POLICY_SNAPSHOT_INVALID")
            result = await self._submit_outcome(claim, outcome, now=now)
            return "failed" if result.status == "accepted" else "stale"
        try:
            handler = self._registry.resolve(claim.request.activity_type)
        except UnknownActivityTypeError:
            outcome = ActivityOutcome.failed(failure_code="UNKNOWN_ACTIVITY_TYPE")
            result = await self._submit_outcome(claim, outcome, now=now)
            return "failed" if result.status == "accepted" else "stale"

        if claim.recovered_after_call_started and not handler.idempotent:
            outcome = ActivityOutcome.unknown(failure_code="NON_IDEMPOTENT_OUTCOME_UNKNOWN")
            result = await self._submit_outcome(claim, outcome, now=now)
            return "unknown" if result.status == "accepted" else "stale"

        if claim.request.timeout_at <= now:
            outcome = ActivityOutcome.failed(failure_code="ACTIVITY_TIMEOUT")
            result = await self._submit_outcome(claim, outcome, now=now)
            return "failed" if result.status == "accepted" else "stale"

        if not await self._store.mark_call_started(claim, now=now):
            return "stale"

        started = await self._submit(
            claim,
            command_type="MarkActivityCallStarted",
            payload={
                "activity_id": str(claim.request.activity_id),
                "generation": claim.request.generation,
                "result_ref": None,
                "result_summary": None,
            },
            now=now,
        )
        if started.status != "accepted":
            return "stale"

        context = ActivityContext(
            worker_id=self._worker_id,
            claim_generation=claim.claim_generation,
            idempotency_key=str(claim.request.activity_id),
            owner_user_id=claim.owner_user_id,
            team_id=claim.team_id,
            run=run_context,
            heartbeat=lambda: self._store.heartbeat(
                claim,
                now=datetime.now(UTC),
                claim_ttl=self._claim_ttl,
            ),
            report_progress=self._progress_reporter(claim, run_context),
        )
        stop_heartbeat = asyncio.Event()
        claim_lost = asyncio.Event()
        handler_task = asyncio.create_task(
            handler.execute(claim.request, context),
            name=f"activity-handler:{claim.request.activity_id}",
        )
        heartbeat_task = asyncio.create_task(
            self._keep_claim_alive(
                claim,
                stop_heartbeat,
                claim_lost,
                handler_task,
            ),
            name=f"activity-heartbeat:{claim.request.activity_id}",
        )
        try:
            remaining_seconds = max(
                0.0,
                (claim.request.timeout_at - now).total_seconds()
                - (time.monotonic() - claim_started),
            )
            outcome = await asyncio.wait_for(
                handler_task,
                timeout=remaining_seconds,
            )
        except TimeoutError:
            outcome = ActivityOutcome.failed(failure_code="ACTIVITY_TIMEOUT")
        except asyncio.CancelledError:
            if claim_lost.is_set():
                return "stale"
            raise
        except Exception as error:
            if isinstance(error, self._infrastructure_errors):
                # A persistence/infrastructure fault during execution is not a
                # handler bug: let it escape to run_once's per-claim
                # classification, which defers the lease-fenced claim with
                # backoff instead of settling the activity as failed (D5).
                raise
            logger.exception(
                "Activity handler failed activity_id=%s activity_type=%s",
                claim.request.activity_id,
                claim.request.activity_type,
            )
            outcome = ActivityOutcome.failed(failure_code="ACTIVITY_HANDLER_ERROR")
        finally:
            stop_heartbeat.set()
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

        # The handler may have run for minutes: settlement uses a fresh clock,
        # not the stale batch-entry ``now`` (P2 幂等复核 / K2-8).
        if outcome.status == "deferred":
            retry_after = timedelta(seconds=outcome.retry_after_seconds or 1.0)
            if not await self._store.defer(
                claim,
                now=datetime.now(UTC),
                retry_after=retry_after,
            ):
                return "stale"
            return "deferred"

        result = await self._submit_outcome(claim, outcome, now=datetime.now(UTC))
        if result.status != "accepted":
            return "stale"
        return outcome.status

    @staticmethod
    def _validate_claim_context(
        claim: ActivityClaim,
        context: RunExecutionContext,
    ) -> None:
        if context.run_id != UUID(claim.request.aggregate_id):
            raise ValueError("Activity Run context identity mismatch")
        scope = context.owner_scope
        if claim.owner_user_id is not None:
            if (
                scope.type != OwnerScopeType.PERSONAL
                or scope.user_id != claim.owner_user_id
                or scope.team_id is not None
            ):
                raise ValueError("Activity Run owner scope mismatch")
            return
        if scope.type != OwnerScopeType.TEAM or scope.team_id != claim.team_id:
            raise ValueError("Activity Run team scope mismatch")

    async def _keep_claim_alive(
        self,
        claim: ActivityClaim,
        stop: asyncio.Event,
        claim_lost: asyncio.Event,
        handler_task: asyncio.Task[ActivityOutcome],
    ) -> None:
        interval = max(0.1, self._claim_ttl.total_seconds() / 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                alive = await self._store.heartbeat(
                    claim,
                    now=datetime.now(UTC),
                    claim_ttl=self._claim_ttl,
                )
                if not alive:
                    claim_lost.set()
                    handler_task.cancel()
                    return

    async def _submit_outcome(
        self,
        claim: ActivityClaim,
        outcome: ActivityOutcome,
        *,
        now: datetime,
    ) -> CommandResult:
        if outcome.status == "succeeded":
            command_type = "CompleteActivity"
            payload = {
                "activity_id": str(claim.request.activity_id),
                "generation": claim.request.generation,
                "result_ref": outcome.result_ref,
                "result_summary": outcome.result_summary,
                "decision_data": outcome.decision_data,
                "public_data": outcome.public_data,
            }
        else:
            command_type = (
                "FailActivity" if outcome.status == "failed" else "MarkActivityOutcomeUnknown"
            )
            payload = {
                "activity_id": str(claim.request.activity_id),
                "generation": claim.request.generation,
                "failure_code": outcome.failure_code,
            }
        return await self._submit(
            claim,
            command_type=command_type,
            payload=payload,
            now=now,
        )

    def _progress_reporter(self, claim: ActivityClaim, run_context: RunExecutionContext):
        """Off-stream telemetry: progress never touches the aggregate stream.

        Reports go straight to the progress sink (public feed + build
        projection). A sink failure is swallowed into ``False`` — progress is
        display-only and must never fail or slow the activity itself.
        """
        sink = self._progress_sink
        if sink is None:
            return None
        sequence = 0

        async def report(payload: dict) -> bool:
            nonlocal sequence
            sequence += 1
            try:
                record = ActivityProgressRecord(
                    run_id=run_context.run_id,
                    activity_id=claim.request.activity_id,
                    generation=claim.request.generation,
                    claim_generation=claim.claim_generation,
                    sequence=sequence,
                    owner_user_id=claim.owner_user_id,
                    team_id=claim.team_id,
                    source_entity_type=run_context.source_entity_type,
                    source_entity_id=run_context.source_entity_id,
                    occurred_at=datetime.now(UTC),
                    **payload,
                )
            except (TypeError, ValueError):
                logger.warning(
                    "拒绝无效进度上报 activity_id=%s payload_keys=%s",
                    claim.request.activity_id,
                    sorted(payload),
                )
                return False
            return await sink.record(record)

        return report

    async def _submit(
        self,
        claim: ActivityClaim,
        *,
        command_type: str,
        payload: dict,
        now: datetime,
        dedupe_suffix: str | None = None,
    ) -> CommandResult:
        command_id = uuid5(
            NAMESPACE_URL,
            ":".join(
                part
                for part in (
                    "opencitadel",
                    str(claim.request.activity_id),
                    command_type,
                    dedupe_suffix,
                )
                if part is not None
            ),
        )
        return await self._run_service.submit(
            RegisteredCommand(
                command_id=command_id,
                command_type=command_type,
                run_id=claim.request.aggregate_id,
                expected_stream_version=None,
                payload=payload,
            ),
            CommandContext(
                owner_user_id=claim.owner_user_id,
                team_id=claim.team_id,
                correlation_id=claim.request.activity_id,
                causation_id=claim.request.activity_id,
                issued_at=now,
            ),
        )


__all__ = ["ActivityBatchStats", "ActivityWorker"]
