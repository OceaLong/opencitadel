"""Crash-boundary contracts for the only durable Activity worker."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.application.execution.activity_registry import ActivityRegistry
from app.application.execution.activity_worker import ActivityWorker
from app.application.execution.orchestrator import CommandResult
from app.domain.execution.activity import (
    ActivityClaim,
    ActivityContext,
    ActivityOutcome,
    ActivityRequest,
)
from app.domain.execution.run import RunFamily, RunState, RunStatus
from tests.app.execution_test_support import (
    run_execution_context_for,
    run_policy_snapshot_json,
)

NOW = datetime(2026, 8, 24, 10, tzinfo=UTC)
RUN_ID = UUID("40000000-0000-0000-0000-000000000001")
ACTIVITY_ID = UUID("40000000-0000-0000-0000-000000000002")


def claim(
    *,
    activity_id: UUID = ACTIVITY_ID,
    recovered: bool = False,
    timeout_at: datetime | None = None,
) -> ActivityClaim:
    return ActivityClaim(
        request=ActivityRequest(
            activity_id=activity_id,
            activity_type="model.call",
            aggregate_type="run",
            aggregate_id=str(RUN_ID),
            generation=0,
            timeout_at=timeout_at or NOW + timedelta(minutes=5),
            input_ref="object://request",
            input_digest="a" * 64,
        ),
        claim_generation=3,
        owner_user_id="user-1",
        team_id=None,
        recovered_after_call_started=recovered,
    )


class FakeStore:
    def __init__(self, claims: tuple[ActivityClaim, ...]) -> None:
        self.claims = claims
        self.started: list[ActivityClaim] = []
        self.allow_start = True
        self.allow_heartbeat = True

    async def claim(self, **kwargs):
        del kwargs
        result, self.claims = self.claims, ()
        return result

    async def mark_call_started(self, candidate, **kwargs):
        del kwargs
        self.started.append(candidate)
        return self.allow_start

    async def heartbeat(self, candidate, **kwargs):
        del candidate, kwargs
        return self.allow_heartbeat


class FakeRunService:
    def __init__(self) -> None:
        self.commands = []

    async def submit(self, command, context):
        self.commands.append((command, context))
        return CommandResult(
            command_id=command.command_id,
            status="accepted",
            first_event_position=1,
            last_event_position=1,
            rejection_code=None,
        )


class FakeRunContexts:
    def __init__(self) -> None:
        self.loaded: list[UUID] = []

    async def load(self, run_id: UUID):
        from app.application.execution.run_context import run_execution_context

        self.loaded.append(run_id)
        return run_execution_context(
            RunState(
                run_id=run_id,
                family=RunFamily.AGENT,
                source_entity_type="session",
                source_entity_id="session-1",
                semantic_payload={},
                policy_snapshot=run_policy_snapshot_json("agent"),
                status=RunStatus.RUNNING,
                stream_version=3,
                owner_user_id="user-1",
                correlation_id=UUID(int=9),
            )
        )


class MismatchedRunContexts:
    async def load(self, run_id: UUID):
        return run_execution_context_for(
            "agent",
            run_id=run_id,
            owner_user_id=None,
            team_id="team-1",
        )


class Handler:
    activity_type = "model.call"

    def __init__(self, *, idempotent: bool) -> None:
        self.idempotent = idempotent
        self.calls: list[tuple[ActivityRequest, ActivityContext]] = []

    async def execute(self, request, context):
        self.calls.append((request, context))
        return ActivityOutcome.succeeded(
            result_ref="object://result",
            result_summary="ok",
        )


class BlockingHandler(Handler):
    async def execute(self, request, context):
        self.calls.append((request, context))
        await __import__("asyncio").Event().wait()
        raise AssertionError("cancelled handler must not return")


class UnexpectedFailureHandler(Handler):
    async def execute(self, request, context):
        self.calls.append((request, context))
        raise LookupError("unexpected adapter failure")


class ProgressHandler(Handler):
    async def execute(self, request, context):
        self.calls.append((request, context))
        assert context.report_progress is not None
        assert await context.report_progress(
            {
                "kind": "step",
                "phase": "parse",
                "status": "started",
                "progress": 0,
                "message": "Parsing",
            }
        )
        assert await context.report_progress(
            {
                "kind": "step",
                "phase": "parse",
                "status": "completed",
                "progress": 10,
                "message": "Parsed",
            }
        )
        return ActivityOutcome.succeeded(
            result_ref=None,
            result_summary="ok",
        )


class ConcurrentHandler(Handler):
    def __init__(self, first_started, second_started, release) -> None:
        super().__init__(idempotent=True)
        self._first_started = first_started
        self._second_started = second_started
        self._release = release

    async def execute(self, request, context):
        self.calls.append((request, context))
        if request.activity_id == ACTIVITY_ID:
            self._first_started.set()
            await self._release.wait()
        else:
            self._second_started.set()
        return ActivityOutcome.succeeded(
            result_ref=f"object://{request.activity_id}",
            result_summary="ok",
        )


@pytest.mark.asyncio
async def test_worker_loads_owning_run_context_before_external_call() -> None:
    store = FakeStore((claim(),))
    service = FakeRunService()
    run_contexts = FakeRunContexts()
    registry = ActivityRegistry()
    handler = Handler(idempotent=True)
    registry.register(handler)
    worker = ActivityWorker(
        store=store,
        run_contexts=run_contexts,
        run_service=service,
        registry=registry,
        worker_id="worker-1",
    )

    stats = await worker.run_once(now=NOW, limit=1)

    assert run_contexts.loaded == [RUN_ID]
    assert store.started == [claim()]
    assert handler.calls[0][1].run.policy_snapshot.family is RunFamily.AGENT
    assert stats.succeeded == 1


@pytest.mark.asyncio
async def test_owner_mismatched_run_context_fails_before_call_started() -> None:
    store = FakeStore((claim(),))
    service = FakeRunService()
    registry = ActivityRegistry()
    handler = Handler(idempotent=True)
    registry.register(handler)
    worker = ActivityWorker(
        store=store,
        run_contexts=MismatchedRunContexts(),
        run_service=service,
        registry=registry,
        worker_id="worker-1",
    )

    stats = await worker.run_once(now=NOW, limit=1)

    assert store.started == []
    assert handler.calls == []
    assert service.commands[0][0].command_type == "FailActivity"
    assert service.commands[0][0].payload["failure_code"] == "POLICY_SNAPSHOT_INVALID"
    assert stats.failed == 1


@pytest.mark.asyncio
async def test_call_started_event_precedes_external_call_and_completion() -> None:
    store = FakeStore((claim(),))
    service = FakeRunService()
    registry = ActivityRegistry()
    handler = Handler(idempotent=True)
    registry.register(handler)
    worker = ActivityWorker(
        store=store,
        run_contexts=FakeRunContexts(),
        run_service=service,
        registry=registry,
        worker_id="worker-1",
    )

    stats = await worker.run_once(now=NOW, limit=10)

    assert [item[0].command_type for item in service.commands] == [
        "MarkActivityCallStarted",
        "CompleteActivity",
    ]
    assert len(handler.calls) == 1
    assert handler.calls[0][1].idempotency_key == str(ACTIVITY_ID)
    assert stats.succeeded == 1


@pytest.mark.asyncio
async def test_claimed_activities_execute_concurrently_within_the_bounded_batch() -> None:
    import asyncio

    second_id = UUID("40000000-0000-0000-0000-000000000003")
    store = FakeStore((claim(), claim(activity_id=second_id)))
    service = FakeRunService()
    registry = ActivityRegistry()
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release = asyncio.Event()
    registry.register(ConcurrentHandler(first_started, second_started, release))
    worker = ActivityWorker(
        store=store,
        run_contexts=FakeRunContexts(),
        run_service=service,
        registry=registry,
        worker_id="worker-1",
    )

    running = asyncio.create_task(worker.run_once(now=NOW, limit=2))
    await first_started.wait()
    try:
        await asyncio.wait_for(second_started.wait(), timeout=0.1)
    finally:
        release.set()
    stats = await running

    assert stats.claimed == 2
    assert stats.succeeded == 2


@pytest.mark.asyncio
async def test_activity_progress_commands_are_ordered_between_start_and_completion() -> None:
    store = FakeStore((claim(),))
    service = FakeRunService()
    registry = ActivityRegistry()
    registry.register(ProgressHandler(idempotent=True))
    worker = ActivityWorker(
        store=store,
        run_contexts=FakeRunContexts(),
        run_service=service,
        registry=registry,
        worker_id="execution-kernel-1",
    )

    await worker.run_once(now=NOW, limit=1)

    assert [item[0].command_type for item in service.commands] == [
        "MarkActivityCallStarted",
        "ReportActivityProgress",
        "ReportActivityProgress",
        "CompleteActivity",
    ]
    assert [item[0].payload.get("sequence") for item in service.commands] == [
        None,
        1,
        2,
        None,
    ]
    assert len({item[0].command_id for item in service.commands}) == 4


@pytest.mark.asyncio
async def test_expired_non_idempotent_call_becomes_unknown_without_replay() -> None:
    store = FakeStore((claim(recovered=True),))
    service = FakeRunService()
    registry = ActivityRegistry()
    handler = Handler(idempotent=False)
    registry.register(handler)
    worker = ActivityWorker(
        store=store,
        run_contexts=FakeRunContexts(),
        run_service=service,
        registry=registry,
        worker_id="worker-1",
    )

    stats = await worker.run_once(now=NOW, limit=10)

    assert handler.calls == []
    assert [item[0].command_type for item in service.commands] == ["MarkActivityOutcomeUnknown"]
    assert stats.unknown == 1


@pytest.mark.asyncio
async def test_expired_idempotent_call_reuses_activity_id_as_provider_key() -> None:
    store = FakeStore((claim(recovered=True),))
    service = FakeRunService()
    registry = ActivityRegistry()
    handler = Handler(idempotent=True)
    registry.register(handler)
    worker = ActivityWorker(
        store=store,
        run_contexts=FakeRunContexts(),
        run_service=service,
        registry=registry,
        worker_id="worker-1",
    )

    stats = await worker.run_once(now=NOW, limit=10)

    assert len(handler.calls) == 1
    assert handler.calls[0][1].idempotency_key == str(ACTIVITY_ID)
    assert stats.succeeded == 1


@pytest.mark.asyncio
async def test_stale_claim_never_invokes_handler() -> None:
    store = FakeStore((claim(),))
    store.allow_start = False
    service = FakeRunService()
    registry = ActivityRegistry()
    handler = Handler(idempotent=True)
    registry.register(handler)
    worker = ActivityWorker(
        store=store,
        run_contexts=FakeRunContexts(),
        run_service=service,
        registry=registry,
        worker_id="worker-1",
    )

    stats = await worker.run_once(now=NOW, limit=10)

    assert handler.calls == []
    assert stats.stale == 1


@pytest.mark.asyncio
async def test_activity_past_its_deadline_fails_without_calling_provider() -> None:
    store = FakeStore((claim(timeout_at=NOW - timedelta(seconds=1)),))
    service = FakeRunService()
    registry = ActivityRegistry()
    handler = Handler(idempotent=True)
    registry.register(handler)
    worker = ActivityWorker(
        store=store,
        run_contexts=FakeRunContexts(),
        run_service=service,
        registry=registry,
        worker_id="worker-1",
    )

    stats = await worker.run_once(now=NOW, limit=10)

    assert handler.calls == []
    assert [item[0].command_type for item in service.commands] == ["FailActivity"]
    assert service.commands[0][0].payload["failure_code"] == "ACTIVITY_TIMEOUT"
    assert stats.failed == 1


@pytest.mark.asyncio
async def test_activity_crossing_deadline_is_cancelled_and_failed() -> None:
    store = FakeStore((claim(timeout_at=NOW + timedelta(milliseconds=30)),))
    service = FakeRunService()
    registry = ActivityRegistry()
    handler = BlockingHandler(idempotent=True)
    registry.register(handler)
    worker = ActivityWorker(
        store=store,
        run_contexts=FakeRunContexts(),
        run_service=service,
        registry=registry,
        worker_id="worker-1",
    )

    stats = await __import__("asyncio").wait_for(
        worker.run_once(now=NOW, limit=1),
        timeout=0.2,
    )

    assert len(handler.calls) == 1
    assert [item[0].command_type for item in service.commands] == [
        "MarkActivityCallStarted",
        "FailActivity",
    ]
    assert service.commands[-1][0].payload["failure_code"] == "ACTIVITY_TIMEOUT"
    assert stats.failed == 1


@pytest.mark.asyncio
async def test_unexpected_handler_exception_fails_activity_without_crashing_worker() -> None:
    store = FakeStore((claim(),))
    service = FakeRunService()
    registry = ActivityRegistry()
    handler = UnexpectedFailureHandler(idempotent=True)
    registry.register(handler)
    worker = ActivityWorker(
        store=store,
        run_contexts=FakeRunContexts(),
        run_service=service,
        registry=registry,
        worker_id="worker-1",
    )

    stats = await worker.run_once(now=NOW, limit=1)

    assert len(handler.calls) == 1
    assert [item[0].command_type for item in service.commands] == [
        "MarkActivityCallStarted",
        "FailActivity",
    ]
    assert service.commands[-1][0].payload["failure_code"] == "ACTIVITY_HANDLER_ERROR"
    assert stats.failed == 1


@pytest.mark.asyncio
async def test_duplicate_empty_wakeup_is_a_noop() -> None:
    worker = ActivityWorker(
        store=FakeStore(()),
        run_contexts=FakeRunContexts(),
        run_service=FakeRunService(),
        registry=ActivityRegistry(),
        worker_id="worker-1",
    )

    stats = await worker.run_once(now=NOW, limit=10)

    assert stats.claimed == 0


@pytest.mark.asyncio
async def test_lost_claim_cancels_inflight_handler_without_reporting_an_outcome() -> None:
    store = FakeStore((claim(),))
    store.allow_heartbeat = False
    service = FakeRunService()
    registry = ActivityRegistry()
    handler = BlockingHandler(idempotent=True)
    registry.register(handler)
    worker = ActivityWorker(
        store=store,
        run_contexts=FakeRunContexts(),
        run_service=service,
        registry=registry,
        worker_id="worker-1",
        claim_ttl=timedelta(milliseconds=30),
    )

    stats = await worker.run_once(now=NOW, limit=1)

    assert len(handler.calls) == 1
    assert [item[0].command_type for item in service.commands] == ["MarkActivityCallStarted"]
    assert stats.stale == 1


class ThrottleHandler(Handler):
    """Records the peak number of handlers executing at once."""

    def __init__(self, release) -> None:
        super().__init__(idempotent=True)
        self._release = release
        self.active = 0
        self.peak = 0
        self.started = 0

    async def execute(self, request, context):
        self.calls.append((request, context))
        self.active += 1
        self.started += 1
        self.peak = max(self.peak, self.active)
        try:
            await self._release.wait()
        finally:
            self.active -= 1
        return ActivityOutcome.succeeded(
            result_ref=f"object://{request.activity_id}",
            result_summary="ok",
        )


@pytest.mark.asyncio
async def test_semaphore_bounds_concurrent_claim_execution_to_configured_ceiling() -> None:
    import asyncio

    max_concurrency = 2
    claims = tuple(
        claim(activity_id=UUID(f"40000000-0000-0000-0000-00000000010{index}")) for index in range(5)
    )
    store = FakeStore(claims)
    service = FakeRunService()
    registry = ActivityRegistry()
    release = asyncio.Event()
    handler = ThrottleHandler(release)
    registry.register(handler)
    worker = ActivityWorker(
        store=store,
        run_contexts=FakeRunContexts(),
        run_service=service,
        registry=registry,
        worker_id="worker-1",
        max_concurrency=max_concurrency,
    )

    running = asyncio.create_task(worker.run_once(now=NOW, limit=len(claims)))
    # Let the event loop fill every available semaphore slot.
    for _ in range(50):
        await asyncio.sleep(0)
        if handler.active >= max_concurrency:
            break
    # The Semaphore must hold execution at the ceiling even though 5 were claimed.
    assert handler.active == max_concurrency
    assert handler.started == max_concurrency
    release.set()
    stats = await running

    assert stats.claimed == len(claims)
    assert stats.succeeded == len(claims)
    assert handler.peak == max_concurrency


@pytest.mark.asyncio
async def test_worker_rejects_non_positive_max_concurrency() -> None:
    store = FakeStore(())
    registry = ActivityRegistry()
    with pytest.raises(ValueError, match="max_concurrency"):
        ActivityWorker(
            store=store,
            run_contexts=FakeRunContexts(),
            run_service=FakeRunService(),
            registry=registry,
            worker_id="worker-1",
            max_concurrency=0,
        )
