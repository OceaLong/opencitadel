"""Local supervision proof; real PostgreSQL fencing is tested separately."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.application.execution.activity_registry import ActivityRegistry
from app.application.execution.activity_worker import (
    ActivityWorker,
    ActivityWorkerUnresponsiveError,
)
from app.domain.execution.activity import ActivityOutcome
from tests.app.application.execution.test_activity_worker import (
    NOW,
    FakeRunContexts,
    FakeRunService,
    FakeStore,
    Handler,
    claim,
)


def worker_for(store, handler, *, service=None, **kwargs):
    registry = ActivityRegistry()
    registry.register(handler)
    return ActivityWorker(
        store=store,
        registry=registry,
        run_contexts=kwargs.pop("run_contexts", FakeRunContexts()),
        run_service=service or FakeRunService(),
        worker_id="worker-1",
        claim_ttl=timedelta(milliseconds=90),
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "timeout", "rejected"])
async def test_heartbeat_failure_stops_handler_and_never_settles(failure):
    cancelled = asyncio.Event()

    class FaultStore(FakeStore):
        async def heartbeat(self, candidate, **kwargs):
            if failure == "exception":
                raise OSError("lease storage unavailable")
            if failure == "timeout":
                await asyncio.Event().wait()

    class WaitingHandler(Handler):
        async def execute(self, request, context):
            self.calls.append((request, context))
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    store, service = FaultStore((claim(),)), FakeRunService()
    handler = WaitingHandler(idempotent=False)
    events = []
    worker = worker_for(store, handler, service=service, lease_observer=events.append)
    stats = await asyncio.wait_for(worker.run_once(now=NOW, limit=1), timeout=0.5)
    assert stats.stale == 1
    assert cancelled.is_set()
    assert not store.deferred
    assert [c.command_type for c, _ in service.commands] == ["MarkActivityCallStarted"]
    assert events.count("lease_lost") == 1
    assert ("heartbeat_rejected" if failure == "rejected" else "heartbeat_error") in events


@pytest.mark.asyncio
async def test_short_heartbeat_fault_recovers_original_claim_and_key():
    recovered = asyncio.Event()

    class RecoveringStore(FakeStore):
        async def heartbeat(self, candidate, **kwargs):
            if not getattr(self, "failed_once", False):
                self.failed_once = True
                raise OSError("brief connection fault")
            result = await super().heartbeat(candidate, **kwargs)
            recovered.set()
            return result

    class WaitingHandler(Handler):
        async def execute(self, request, context):
            self.calls.append((request, context))
            await recovered.wait()
            return ActivityOutcome.succeeded(result_ref="object://result")

    store, service = RecoveringStore((claim(),)), FakeRunService()
    handler = WaitingHandler(idempotent=False)
    events = []
    stats = await asyncio.wait_for(
        worker_for(store, handler, service=service, lease_observer=events.append).run_once(
            now=NOW, limit=1
        ),
        timeout=0.5,
    )
    assert stats.succeeded == 1
    assert len(handler.calls) == 1
    assert handler.calls[0][1].idempotency_key == str(claim().request.activity_id)
    assert {ctx.activity_lease_proof.claim_generation for _, ctx in service.commands} == {3}
    assert events == ["heartbeat_error", "heartbeat_recovered"]


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_at", ["content", "settlement"])
async def test_heartbeat_covers_content_and_final_settlement(blocked_at):
    class SlowService(FakeRunService):
        async def submit(self, command, context):
            if blocked_at == "settlement" and command.command_type == "CompleteActivity":
                await asyncio.sleep(0.2)
            return await super().submit(command, context)

    class SlowContent:
        async def prepare(self, claim, command_id, command_type, payload, **kwargs):
            if blocked_at == "content" and command_type == "CompleteActivity":
                await asyncio.sleep(0.2)

    store = FakeStore((claim(),))
    worker = worker_for(
        store, Handler(idempotent=True), service=SlowService(), content_writer=SlowContent()
    )
    stats = await asyncio.wait_for(worker.run_once(now=NOW, limit=1), timeout=0.6)
    assert stats.succeeded == 1
    assert store.heartbeat_calls >= 4


@pytest.mark.asyncio
async def test_lease_loss_while_saving_content_prevents_submit_even_if_save_returns():
    saving = asyncio.Event()

    class LostDuringSave(FakeStore):
        async def heartbeat(self, candidate, **kwargs):
            await saving.wait()

    class CancellationSuppressingContent:
        async def prepare(self, claim, command_id, command_type, payload, **kwargs):
            if command_type == "CompleteActivity":
                saving.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    return

    service = FakeRunService()
    worker = worker_for(
        LostDuringSave((claim(),)),
        Handler(idempotent=True),
        service=service,
        content_writer=CancellationSuppressingContent(),
    )
    stats = await asyncio.wait_for(worker.run_once(now=NOW, limit=1), timeout=0.5)
    assert stats.stale == 1
    assert [c.command_type for c, _ in service.commands] == ["MarkActivityCallStarted"]


@pytest.mark.asyncio
async def test_free_slot_is_refilled_before_other_claim_finishes():
    first, second, third = (claim(activity_id=uuid4()) for _ in range(3))
    release = asyncio.Event()
    third_started = asyncio.Event()

    class MixedHandler(Handler):
        async def execute(self, request, context):
            self.calls.append((request, context))
            if request.activity_id == first.request.activity_id:
                await release.wait()
            if request.activity_id == third.request.activity_id:
                third_started.set()
            return ActivityOutcome.succeeded(result_ref=None)

    store = FakeStore((first, second, third))
    worker = worker_for(store, MixedHandler(idempotent=True), max_concurrency=2)
    running = asyncio.create_task(worker.run_once(now=NOW, limit=3))
    try:
        await asyncio.wait_for(third_started.wait(), timeout=0.5)
        assert not release.is_set()
        assert store.claim_limits == [2, 1]
    finally:
        release.set()
        stats = await running
    assert stats.succeeded == 3


@pytest.mark.asyncio
async def test_context_load_cannot_extend_business_deadline():
    class SlowContexts(FakeRunContexts):
        async def load(self, run_id):
            await asyncio.sleep(0.05)
            return await super().load(run_id)

    store = FakeStore((claim(timeout_at=NOW + timedelta(milliseconds=20)),))
    service, handler = FakeRunService(), Handler(idempotent=True)
    worker = worker_for(store, handler, service=service, run_contexts=SlowContexts())
    stats = await worker.run_once(now=NOW, limit=1)
    assert stats.stale == 1
    assert not handler.calls
    assert not store.started
    assert service.commands == []


@pytest.mark.asyncio
async def test_claim_response_latency_does_not_extend_local_lease():
    class DelayedClaimStore(FakeStore):
        async def claim(self, **kwargs):
            granted = await super().claim(**kwargs)
            await asyncio.sleep(0.11)
            return granted

    store, handler, service = (
        DelayedClaimStore((claim(),)),
        Handler(idempotent=True),
        FakeRunService(),
    )
    stats = await worker_for(store, handler, service=service).run_once(now=NOW, limit=1)
    assert stats.stale == 1
    assert not handler.calls
    assert not store.started
    assert service.commands == []


@pytest.mark.asyncio
async def test_non_idempotent_infrastructure_error_after_start_is_unknown():
    class FailedWrite(Handler):
        async def execute(self, request, context):
            raise OSError("connection lost after external send")

    store, service = FakeStore((claim(),)), FakeRunService()
    stats = await worker_for(store, FailedWrite(idempotent=False), service=service).run_once(
        now=NOW, limit=1
    )
    assert stats.unknown == 1
    assert not store.deferred
    assert service.commands[-1][0].command_type == "MarkActivityOutcomeUnknown"


@pytest.mark.asyncio
async def test_unresponsive_handler_quarantines_slot_and_stops_further_claims():
    release = asyncio.Event()
    exited = asyncio.Event()

    class UnresponsiveHandler(Handler):
        async def execute(self, request, context):
            try:
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        continue
                return ActivityOutcome.succeeded(result_ref="object://late")
            finally:
                exited.set()

    store, service = FakeStore((claim(), claim(activity_id=uuid4()))), FakeRunService()
    store.allow_heartbeat = False
    worker = worker_for(
        store,
        UnresponsiveHandler(idempotent=False),
        service=service,
        max_concurrency=1,
        cancellation_grace_seconds=0.02,
    )
    try:
        with pytest.raises(ActivityWorkerUnresponsiveError):
            await asyncio.wait_for(worker.run_once(now=NOW, limit=1), timeout=0.5)
        with pytest.raises(ActivityWorkerUnresponsiveError):
            await worker.run_once(now=NOW, limit=1)
        assert len(store.claims) == 1
        assert [c.command_type for c, _ in service.commands] == ["MarkActivityCallStarted"]
    finally:
        release.set()
        await asyncio.wait_for(exited.wait(), timeout=0.2)
        await asyncio.gather(*worker._drainers)


@pytest.mark.asyncio
async def test_expected_deferral_does_not_spend_poison_attempt():
    class DeferredHandler(Handler):
        async def execute(self, request, context):
            return ActivityOutcome.deferred(retry_after_seconds=1)

    store = FakeStore((claim(),))
    stats = await worker_for(store, DeferredHandler(idempotent=True)).run_once(now=NOW, limit=1)
    assert stats.deferred == 1
    assert store.consume_attempts == [False]


@pytest.mark.asyncio
async def test_continuous_slots_accept_new_work_while_long_claim_is_running():
    timeout_at = datetime.now(UTC) + timedelta(minutes=1)
    long_claim = claim(activity_id=uuid4(), timeout_at=timeout_at)
    new_claim = claim(activity_id=uuid4(), timeout_at=timeout_at)
    long_started, new_started, release, stop = (asyncio.Event() for _ in range(4))

    class HandlerWithArrival(Handler):
        async def execute(self, request, context):
            if request.activity_id == long_claim.request.activity_id:
                long_started.set()
                await release.wait()
            else:
                new_started.set()
            return ActivityOutcome.succeeded(result_ref=None)

    store = FakeStore((long_claim,))
    worker = worker_for(store, HandlerWithArrival(idempotent=True), max_concurrency=2)
    running = asyncio.create_task(worker.serve(stop=stop, idle_poll_seconds=0.01))
    try:
        await asyncio.wait_for(long_started.wait(), timeout=0.5)
        store.claims = (new_claim,)
        await asyncio.wait_for(new_started.wait(), timeout=0.5)
        assert not release.is_set()
        stop.set()
    finally:
        release.set()
        stop.set()
        await asyncio.wait_for(running, timeout=0.5)


@pytest.mark.asyncio
async def test_shutdown_grace_cancels_work_without_settlement_or_new_claims():
    started, cancelled, stop = (asyncio.Event() for _ in range(3))

    class WaitingHandler(Handler):
        async def execute(self, request, context):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    store = FakeStore((claim(timeout_at=datetime.now(UTC) + timedelta(minutes=1)),))
    service = FakeRunService()
    worker = worker_for(store, WaitingHandler(idempotent=False), service=service, max_concurrency=1)
    running = asyncio.create_task(
        worker.serve(stop=stop, idle_poll_seconds=0.01, shutdown_grace_seconds=0.02)
    )
    await asyncio.wait_for(started.wait(), timeout=0.5)
    stop.set()
    await asyncio.wait_for(running, timeout=0.5)
    assert cancelled.is_set()
    assert len(store.claim_limits) == 1
    assert [command.command_type for command, _ in service.commands] == ["MarkActivityCallStarted"]


@pytest.mark.asyncio
async def test_continuous_slot_retries_transient_claim_outage_without_losing_capacity():
    stop = asyncio.Event()

    class OutageStore(FakeStore):
        failures = 0

        async def claim(self, **kwargs):
            if not self.failures:
                self.failures += 1
                raise OSError("temporary claim outage")
            return await super().claim(**kwargs)

    class FinishingHandler(Handler):
        async def execute(self, request, context):
            stop.set()
            return ActivityOutcome.succeeded(result_ref=None)

    store = OutageStore((claim(timeout_at=datetime.now(UTC) + timedelta(minutes=1)),))
    service = FakeRunService()
    worker = worker_for(
        store, FinishingHandler(idempotent=True), service=service, max_concurrency=1
    )
    await asyncio.wait_for(worker.serve(stop=stop, idle_poll_seconds=0.01), timeout=0.5)
    assert store.failures == 1
    assert service.commands[-1][0].command_type == "CompleteActivity"
