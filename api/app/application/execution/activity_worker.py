"""Lease-fenced worker for durable external Activities."""

from __future__ import annotations

import asyncio
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
from app.application.execution.run_context import RunContextSource
from app.domain.execution.activity import (
    ActivityClaim,
    ActivityContext,
    ActivityOutcome,
)
from app.domain.execution.commands import CommandContext, RegisteredCommand
from app.domain.execution.context import RunExecutionContext
from app.domain.models.scope import OwnerScopeType
from app.domain.runtime_policy.errors import RuntimePolicyIntegrityError


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
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        self._store = store
        self._run_contexts = run_contexts
        self._run_service = run_service
        self._registry = registry
        self._worker_id = worker_id
        self._claim_ttl = claim_ttl

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
        batch_started = time.monotonic()
        for claim in claims:
            elapsed = time.monotonic() - batch_started
            status = await self._execute_claim(
                claim,
                now=now + timedelta(seconds=elapsed),
            )
            counts[status] += 1
        return ActivityBatchStats(**counts)

    async def _execute_claim(
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
            report_progress=self._progress_reporter(claim),
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
        except (OSError, RuntimeError, ValueError):
            outcome = ActivityOutcome.failed(failure_code="ACTIVITY_HANDLER_ERROR")
        finally:
            stop_heartbeat.set()
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

        if outcome.status == "deferred":
            retry_after = timedelta(seconds=outcome.retry_after_seconds or 1.0)
            if not await self._store.defer(
                claim,
                now=now,
                retry_after=retry_after,
            ):
                return "stale"
            return "deferred"

        result = await self._submit_outcome(claim, outcome, now=now)
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

    def _progress_reporter(self, claim: ActivityClaim):
        sequence = 0

        async def report(payload: dict) -> bool:
            nonlocal sequence
            sequence += 1
            result = await self._submit(
                claim,
                command_type="ReportActivityProgress",
                payload={
                    "activity_id": str(claim.request.activity_id),
                    "generation": claim.request.generation,
                    "sequence": sequence,
                    **payload,
                },
                now=datetime.now(UTC),
                dedupe_suffix=str(sequence),
            )
            return result.status == "accepted"

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
