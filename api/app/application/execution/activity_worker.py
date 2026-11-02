"""Lease-fenced worker for durable external Activities."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
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
from app.application.ports.inference_dispatch import activity_lease_context
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.execution.activity import (
    ActivityClaim,
    ActivityContext,
    ActivityLeaseConfirmation,
    ActivityOutcome,
)
from app.domain.execution.commands import CommandContext, RegisteredCommand
from app.domain.execution.context import RunExecutionContext
from app.domain.execution.definition import DefinitionIntegrityError, DefinitionUnavailable
from app.domain.models.scope import OwnerScopeType
from app.domain.runtime_policy.errors import RuntimePolicyIntegrityError
from core.otel import get_tracer

logger = logging.getLogger(__name__)
_tracer = get_tracer("opencitadel.execution.activity")

# Exception types treated as transient worker infrastructure faults (K2-2/D5):
# before an unsafe external call, these defer with backoff rather than settle
# the activity as failed. After a non-idempotent call they remain unknown.
# The persistence adapter extends this tuple with its own
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


class ActivityLeaseLostError(RuntimeError):
    """The local worker no longer has confirmed permission to make progress."""


class ActivityWorkerUnresponsiveError(RuntimeError):
    """A cancelled handler did not exit; this worker must stop admitting work."""


@dataclass
class _LeaseState:
    deadline: float
    timeout_deadline: float
    lost: bool = False
    active: asyncio.Event = field(default_factory=asyncio.Event)
    renewal_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    children: set[asyncio.Task] = field(default_factory=set)

    def require_active(self) -> None:
        if (
            self.lost
            or not self.active.is_set()
            or time.monotonic() >= min(self.deadline, self.timeout_deadline)
        ):
            raise ActivityLeaseLostError("Activity lease is lost or unconfirmed")

    async def wait_active(self) -> None:
        await self.active.wait()
        self.require_active()

    def lose(self) -> None:
        self.lost = True
        self.active.set()  # Wake blocked submitters, which then reject locally.


_current_lease: ContextVar[_LeaseState | None] = ContextVar("activity_lease", default=None)


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
    ) -> ActivityLeaseConfirmation | None: ...

    async def defer(
        self,
        claim: ActivityClaim,
        *,
        now: datetime,
        retry_after: timedelta,
        consume_attempt: bool = False,
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
        definitions=None,
        progress_sink: ActivityProgressSink | None = None,
        content_writer=None,
        execution_gate=None,
        infrastructure_errors: tuple[type[Exception], ...] = DEFAULT_INFRASTRUCTURE_ERRORS,
        cancellation_grace_seconds: float = 1.0,
        lease_observer: Callable[[str], None] | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        if cancellation_grace_seconds <= 0:
            raise ValueError("cancellation_grace_seconds must be positive")
        self._store = store
        self._run_contexts = run_contexts
        self._run_service = run_service
        self._registry = registry
        self._definitions = definitions
        self._worker_id = worker_id
        self._claim_ttl = claim_ttl
        self._max_concurrency = max_concurrency
        self._progress_sink = progress_sink
        self._content_writer = content_writer
        self._execution_gate = execution_gate
        self._infrastructure_errors = infrastructure_errors
        self._cancellation_grace_seconds = cancellation_grace_seconds
        self._lease_observer = lease_observer
        self._run_lock = asyncio.Lock()
        self._quarantined: set[asyncio.Task] = set()
        self._drainers: set[asyncio.Task] = set()
        self._unresponsive = False
        # A slot is reserved BEFORE asking PostgreSQL to grant its lease.
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def _observe_lease(self, event: str) -> None:
        if self._lease_observer is not None:
            try:
                self._lease_observer(event)
            except Exception:
                logger.warning("Activity lease observer failed", exc_info=True)

    async def run_once(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> ActivityBatchStats:
        if limit <= 0:
            raise ValueError("limit must be positive")
        async with self._run_lock:
            return await self._run_batch(now=now, limit=limit)

    async def serve(
        self,
        *,
        stop: asyncio.Event,
        idle_poll_seconds: float,
        shutdown_grace_seconds: float = 30.0,
    ) -> None:
        """Continuously refill each slot without waiting for a batch barrier.

        run_once remains a bounded administration/test operation. The process
        activity plane uses this method so a long claim never holds up another
        slot when a batch counter is exhausted.
        """
        if idle_poll_seconds <= 0 or shutdown_grace_seconds <= 0:
            raise ValueError("poll and shutdown grace must be positive")

        async def wait_for_work() -> None:
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=idle_poll_seconds)

        async def slot_loop() -> None:
            while not stop.is_set():
                if self._unresponsive:
                    raise ActivityWorkerUnresponsiveError("Activity worker has quarantined work")
                await self._semaphore.acquire()
                requested_at = time.monotonic()
                try:
                    claims = await self._store.claim(
                        now=datetime.now(UTC),
                        limit=1,
                        worker_id=self._worker_id,
                        claim_ttl=self._claim_ttl,
                    )
                except self._infrastructure_errors:
                    self._semaphore.release()
                    logger.warning("Activity slot claim failed", exc_info=True)
                    await wait_for_work()
                    continue
                except BaseException:
                    self._semaphore.release()
                    raise
                if not claims:
                    self._semaphore.release()
                    await wait_for_work()
                    continue
                if len(claims) != 1:
                    self._semaphore.release()
                    raise RuntimeError("Activity store exceeded reserved slot")
                await self._execute_claim(
                    claims[0], now=datetime.now(UTC), reserved=True, requested_at=requested_at
                )

        async with self._run_lock:
            slots = {
                asyncio.create_task(slot_loop(), name=f"activity-slot:{index}")
                for index in range(self._max_concurrency)
            }
            stopping = asyncio.create_task(stop.wait(), name="activity-stop")
            try:
                done, _ = await asyncio.wait(
                    (*slots, stopping), return_when=asyncio.FIRST_COMPLETED
                )
                for task in done - {stopping}:
                    task.result()
                # Stop admission immediately; let supervised ownership drain
                # within both its lease/deadline and this process grace.
                _, pending = await asyncio.wait(slots, timeout=shutdown_grace_seconds)
                for task in pending:
                    task.cancel()
                results = await asyncio.gather(*slots, return_exceptions=True)
                for result in results:
                    if isinstance(result, ActivityWorkerUnresponsiveError):
                        raise result
            finally:
                stopping.cancel()
                for task in slots:
                    task.cancel()
                await asyncio.gather(stopping, *slots, return_exceptions=True)

    async def _run_batch(self, *, now: datetime, limit: int) -> ActivityBatchStats:
        if self._unresponsive:
            raise ActivityWorkerUnresponsiveError("Activity worker has quarantined work")
        counts = {
            "claimed": 0,
            "succeeded": 0,
            "failed": 0,
            "unknown": 0,
            "stale": 0,
            "deferred": 0,
        }
        pending: dict[asyncio.Task[str], ActivityClaim] = {}
        exhausted = False
        try:
            while pending or (counts["claimed"] < limit and not exhausted):
                free = min(self._max_concurrency - len(pending), limit - counts["claimed"])
                if free > 0 and not exhausted:
                    for _ in range(free):
                        await self._semaphore.acquire()
                    requested_at = time.monotonic()
                    try:
                        claims = await self._store.claim(
                            now=now,
                            limit=free,
                            worker_id=self._worker_id,
                            claim_ttl=self._claim_ttl,
                        )
                    except BaseException:
                        for _ in range(free):
                            self._semaphore.release()
                        raise
                    if len(claims) > free:
                        for _ in range(free):
                            self._semaphore.release()
                        raise RuntimeError("Activity store exceeded reserved slots")
                    for _ in range(free - len(claims)):
                        self._semaphore.release()
                    counts["claimed"] += len(claims)
                    exhausted = len(claims) < free
                    for claim in claims:
                        task = asyncio.create_task(
                            self._execute_claim(
                                claim, now=now, reserved=True, requested_at=requested_at
                            ),
                            name=f"activity-claim:{claim.request.activity_id}",
                        )
                        pending[task] = claim
                if not pending:
                    break
                done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    pending.pop(task)
                    counts[task.result()] += 1
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
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
        if isinstance(error, ActivityLeaseLostError):
            return "stale"
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
                consume_attempt=not isinstance(
                    error, (RunContextUnavailableError, *self._infrastructure_errors)
                ),
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
        reserved: bool = False,
        requested_at: float | None = None,
    ) -> str:
        if not reserved:
            await self._semaphore.acquire()
        release_slot = True
        origin = requested_at if requested_at is not None else time.monotonic()
        lease = _LeaseState(
            deadline=self._safe_deadline(origin, claim.server_now, claim.lease_deadline),
            timeout_deadline=origin + (claim.request.timeout_at - claim.server_now).total_seconds(),
        )
        lease.active.set()

        async def execute() -> str:
            token = _current_lease.set(lease)
            try:
                lease.require_active()
                if claim.worker_id != self._worker_id:
                    raise ActivityLeaseLostError("Activity belongs to another worker")
                try:
                    with activity_lease_context(lease.require_active):
                        return await self._execute_claim_span(claim, now=now)
                except ActivityWorkerUnresponsiveError:
                    raise
                except Exception as error:  # noqa: BLE001 - supervised claim boundary
                    await lease.wait_active()
                    return await self._absorb_claim_failure(claim, error)
            finally:
                _current_lease.reset(token)

        task = asyncio.create_task(execute(), name=f"activity-owned:{claim.request.activity_id}")
        heartbeat = asyncio.create_task(
            self._keep_claim_alive(claim, lease),
            name=f"activity-heartbeat:{claim.request.activity_id}",
        )
        try:
            while True:
                deadline = min(lease.deadline, lease.timeout_deadline)
                remaining = max(0.0, deadline - time.monotonic())
                done, _ = await asyncio.wait(
                    (task, heartbeat),
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if task in done:
                    try:
                        result = task.result()
                        if result == "stale":
                            self._observe_lease("lease_lost")
                        return result
                    except ActivityLeaseLostError:
                        self._observe_lease("lease_lost")
                        return "stale"
                if (
                    heartbeat in done
                    or lease.lost
                    or time.monotonic() >= min(lease.deadline, lease.timeout_deadline)
                ):
                    break
            # Any supervisor failure fails closed, including bugs unrelated to
            # the database. Never leave an unsupervised handler running.
            if heartbeat.done() and not heartbeat.cancelled() and heartbeat.exception() is not None:
                logger.error("Activity lease supervisor failed", exc_info=heartbeat.exception())
            lease.lose()
            self._observe_lease("lease_lost")
            return "stale"
        finally:
            lease.lose()
            cleanup_tasks = {task, heartbeat, *lease.children}
            for cleanup_task in cleanup_tasks:
                cleanup_task.cancel()
            done, pending = await asyncio.wait(
                cleanup_tasks, timeout=self._cancellation_grace_seconds
            )
            for finished in done:
                if not finished.cancelled():
                    finished.exception()
            if pending:
                self._observe_lease("cancellation_timeout")
                # Retain the slot while Python code still runs. The process
                # owner sees a fail-stop error and must restart this worker.
                release_slot = False
                self._unresponsive = True
                self._quarantined.update(pending)

                async def release_when_stopped() -> None:
                    await asyncio.gather(*pending, return_exceptions=True)
                    self._quarantined.difference_update(pending)
                    self._semaphore.release()

                drainer = asyncio.create_task(
                    release_when_stopped(), name="activity-quarantine-drain"
                )
                self._drainers.add(drainer)
                drainer.add_done_callback(self._drainers.discard)
            if release_slot:
                self._semaphore.release()
            else:
                raise ActivityWorkerUnresponsiveError("Activity cancellation exceeded grace")

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
        lease = _current_lease.get()
        assert lease is not None
        try:
            run_context = await self._run_contexts.load(UUID(claim.request.aggregate_id))
            self._validate_claim_context(claim, run_context)
        except (RuntimePolicyIntegrityError, ValueError):
            outcome = ActivityOutcome.failed(failure_code="POLICY_SNAPSHOT_INVALID")
            result = await self._submit_outcome(claim, outcome, now=now)
            return "failed" if result.status == "accepted" else "stale"
        try:
            version = "1"
            if run_context.definition_pin is not None:
                if self._definitions is None:
                    raise DefinitionUnavailable("no exact local Activity definition")
                registered = self._definitions.resolve(run_context.definition_pin)
                version = registered.manifest.handler_version(claim.request.activity_type)
                if (
                    claim.request.definition_digest != registered.manifest.digest
                    or claim.request.handler_version != version
                ):
                    raise DefinitionIntegrityError(
                        "Activity routing differs from its formal definition"
                    )
            elif self._definitions is not None:
                raise DefinitionUnavailable("legacy Activity has no reliable implementation pin")
            handler = self._registry.resolve(claim.request.activity_type, handler_version=version)
        except DefinitionUnavailable:
            await self._store.defer(
                claim, now=now, retry_after=timedelta(seconds=2), consume_attempt=False
            )
            return "deferred"
        except UnknownActivityTypeError:
            outcome = ActivityOutcome.failed(failure_code="UNKNOWN_ACTIVITY_TYPE")
            result = await self._submit_outcome(claim, outcome, now=now)
            return "failed" if result.status == "accepted" else "stale"

        recovery_safe = False
        if not handler.idempotent:
            check_recovery = getattr(handler, "recovery_safe", None)
            if check_recovery is not None:
                try:
                    recovery_safe = await check_recovery(claim.request, run_context)
                except ReplayMismatch:
                    outcome = ActivityOutcome.failed(failure_code=ReplayMismatch.code)
                    result = await self._submit_outcome(claim, outcome, now=now)
                    return "failed" if result.status == "accepted" else "stale"
        if claim.recovered_after_call_started and not handler.idempotent and not recovery_safe:
            outcome = ActivityOutcome.unknown(failure_code="NON_IDEMPOTENT_OUTCOME_UNKNOWN")
            result = await self._submit_outcome(claim, outcome, now=now)
            return "unknown" if result.status == "accepted" else "stale"

        if time.monotonic() >= lease.timeout_deadline:
            return "stale"  # Only the trusted timeout path may settle an expired request.

        if self._execution_gate is not None:
            await self._execution_gate.before_activity(claim, run_context)

        await lease.wait_active()
        if time.monotonic() >= lease.timeout_deadline:
            return "stale"
        if not await self._store.mark_call_started(claim, now=now):
            return "stale"

        started = await self._submit(
            claim,
            command_type="MarkActivityCallStarted",
            payload={
                "activity_id": str(claim.request.activity_id),
                "generation": claim.request.generation,
                "claim_generation": claim.claim_generation,
                "result_ref": None,
                "result_summary": None,
            },
            now=now,
            dedupe_suffix=f"{claim.request.generation}:{claim.claim_generation}",
        )
        if started.status != "accepted":
            return "stale"

        trusted_citations = []

        async def record_citations(items):
            from app.domain.models.knowledge_citation import KnowledgeCitation

            if not all(isinstance(item, KnowledgeCitation) for item in items):
                raise ValueError("only typed retrieval citations may be captured")
            trusted_citations.extend(items)

        context = ActivityContext(
            record_citations=record_citations,
            activity_id=claim.request.activity_id,
            generation=claim.request.generation,
            worker_id=self._worker_id,
            claim_generation=claim.claim_generation,
            idempotency_key=str(claim.request.activity_id),
            owner_user_id=claim.owner_user_id,
            team_id=claim.team_id,
            run=run_context,
            heartbeat=lambda: self._renew_lease(claim, lease),
            require_active_lease=lease.require_active,
            report_progress=self._progress_reporter(claim, run_context),
        )
        await lease.wait_active()
        if time.monotonic() >= lease.timeout_deadline:
            return "stale"
        handler_task = asyncio.create_task(
            handler.execute(claim.request, context),
            name=f"activity-handler:{claim.request.activity_id}",
        )
        lease.children.add(handler_task)
        try:
            remaining_seconds = max(0.0, lease.timeout_deadline - time.monotonic())
            done, _ = await asyncio.wait((handler_task,), timeout=remaining_seconds)
            if not done:
                handler_task.cancel()
                done, _ = await asyncio.wait(
                    (handler_task,), timeout=self._cancellation_grace_seconds
                )
                if not done:
                    raise ActivityWorkerUnresponsiveError("Handler ignored timeout cancellation")
                return "stale"
            outcome = handler_task.result()
        except asyncio.CancelledError:
            raise
        except ActivityWorkerUnresponsiveError:
            raise
        except ReplayMismatch:
            outcome = ActivityOutcome.failed(failure_code=ReplayMismatch.code)
        except Exception as error:
            if isinstance(error, self._infrastructure_errors) and (
                handler.idempotent or recovery_safe
            ):
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
            outcome = (
                ActivityOutcome.failed(failure_code="ACTIVITY_HANDLER_ERROR")
                if handler.idempotent or recovery_safe
                else ActivityOutcome.unknown(failure_code="NON_IDEMPOTENT_OUTCOME_UNKNOWN")
            )
        # The handler may have run for minutes: settlement uses a fresh clock,
        # not the stale batch-entry ``now`` (P2 幂等复核 / K2-8).
        if outcome.status == "deferred":
            await lease.wait_active()
            retry_after = timedelta(seconds=outcome.retry_after_seconds or 1.0)
            if not await self._store.defer(
                claim,
                now=datetime.now(UTC),
                retry_after=retry_after,
            ):
                return "stale"
            return "deferred"

        result = await self._submit_outcome(
            claim, outcome, now=datetime.now(UTC), content_citations=trusted_citations
        )
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

    @staticmethod
    def _safe_deadline(started_at: float, server_now: datetime, deadline: datetime) -> float:
        duration = max(0.0, (deadline - server_now).total_seconds())
        return started_at + duration - min(0.05, duration * 0.05)

    async def _renew_lease(self, claim: ActivityClaim, lease: _LeaseState) -> bool:
        async with lease.renewal_lock:
            if lease.lost or time.monotonic() >= min(lease.deadline, lease.timeout_deadline):
                lease.lose()
                return False
            started_at = time.monotonic()
            try:
                confirmation = await asyncio.wait_for(
                    self._store.heartbeat(claim, now=datetime.now(UTC), claim_ttl=self._claim_ttl),
                    # Leave time to retry while authority is still valid.
                    timeout=min(
                        self._claim_ttl.total_seconds() / 6,
                        max(0.0, min(lease.deadline, lease.timeout_deadline) - started_at) / 2,
                    ),
                )
            except Exception:
                lease.active.clear()
                self._observe_lease("heartbeat_error")
                logger.warning(
                    "Activity heartbeat failed activity_id=%s claim_generation=%s",
                    claim.request.activity_id,
                    claim.claim_generation,
                    exc_info=True,
                )
                if time.monotonic() >= min(lease.deadline, lease.timeout_deadline):
                    lease.lose()
                return False
            if confirmation is None:
                self._observe_lease("heartbeat_rejected")
                lease.lose()
                return False
            # A late response cannot resurrect local authority, even if the
            # server happened to extend the row before its response was lost.
            if lease.lost or time.monotonic() >= min(lease.deadline, lease.timeout_deadline):
                lease.lose()
                return False
            lease.deadline = self._safe_deadline(
                started_at, confirmation.server_now, confirmation.lease_deadline
            )
            if time.monotonic() >= min(lease.deadline, lease.timeout_deadline):
                lease.lose()
                return False
            if not lease.active.is_set():
                self._observe_lease("heartbeat_recovered")
            lease.active.set()
            return True

    async def _keep_claim_alive(self, claim: ActivityClaim, lease: _LeaseState) -> None:
        interval = self._claim_ttl.total_seconds() / 3
        while not lease.lost:
            remaining = min(lease.deadline, lease.timeout_deadline) - time.monotonic()
            if remaining <= 0:
                lease.lose()
                return
            await asyncio.sleep(min(interval, remaining))
            await self._renew_lease(claim, lease)

    async def _submit_outcome(
        self,
        claim: ActivityClaim,
        outcome: ActivityOutcome,
        *,
        now: datetime,
        content_citations=(),
    ) -> CommandResult:
        if outcome.status == "succeeded":
            command_type = "CompleteActivity"
            payload = {
                "activity_id": str(claim.request.activity_id),
                "generation": claim.request.generation,
                "claim_generation": claim.claim_generation,
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
                "claim_generation": claim.claim_generation,
                "failure_code": outcome.failure_code,
            }
            if outcome.status == "failed":
                payload["public_data"] = outcome.public_data
        return await self._submit(
            claim,
            command_type=command_type,
            payload=payload,
            now=now,
            content_citations=content_citations,
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
        content_citations=(),
    ) -> CommandResult:
        command_id = uuid5(
            NAMESPACE_URL,
            ":".join(
                part
                for part in (
                    "opencitadel",
                    str(claim.request.activity_id),
                    command_type,
                    dedupe_suffix or f"{claim.request.generation}:{claim.claim_generation}",
                )
                if part is not None
            ),
        )
        lease = _current_lease.get()
        assert lease is not None
        await lease.wait_active()
        if self._content_writer is not None:
            await self._content_writer.prepare(
                claim, command_id, command_type, payload, citations=content_citations
            )
        await lease.wait_active()
        return await self._run_service.submit(
            RegisteredCommand(
                command_id=command_id,
                command_type=command_type,
                command_schema_version=3 if command_type == "FailActivity" else 2,
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
                activity_lease_proof=claim.lease_proof,
            ),
        )


__all__ = ["ActivityBatchStats", "ActivityWorker"]
