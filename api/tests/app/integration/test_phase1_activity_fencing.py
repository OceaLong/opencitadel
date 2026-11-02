"""Phase 1 acceptance: actual DB leases, transactions and independent workers."""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.execution.activity_registry import create_activity_registry
from app.domain.execution.commands import ActivityLeaseProof, CommandEnvelope
from app.domain.execution.run import RunAggregate
from app.domain.models.artifact_provenance import ArtifactProducer
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.resource_pin import ResourceUnavailable
from app.domain.models.scope import OwnerScope
from app.infrastructure.adapters.execution_ports import SqlAlchemyTimerDispatcher
from app.infrastructure.execution.models import (
    ExecutionActivityTaskORM,
    ExecutionCommandInboxORM,
    ExecutionEventORM,
)
from app.infrastructure.execution.postgres_activity_fence import (
    ActivityLeaseExpiredError,
    authorize_activity_command,
    ensure_activity_lease_current,
)
from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
from app.infrastructure.execution.postgres_activity_timeout import PostgresActivityTimeoutGuard
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter
from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
from app.infrastructure.execution.postgres_inbox_source import PostgresInboxSource
from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.execution_database_fixture import isolated_execution_database  # noqa: F401
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_session,
    execution_kernel_database_uri,
    run_policy_snapshot_json,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("isolated_execution_database")]
AUTH = AuthorizationContext.system("phase1-fencing-test")


@pytest.fixture
async def runtime():
    engine = create_async_engine(execution_kernel_database_uri())
    sessions = authenticated_session_factory(
        engine, signing_secret=load_deployment_settings().session_secret
    )
    orchestrator = SqlAlchemyExecutionOrchestrator(
        session_factory=sessions, aggregates={"run": RunAggregate()}, authorization=AUTH
    )
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "CREATE TABLE phase1_physical_calls(activity_id uuid NOT NULL,worker_id text NOT NULL,operation_key text NOT NULL,sent_at timestamptz NOT NULL DEFAULT clock_timestamp())"
            )
        )
        await db.execute(
            text("GRANT SELECT, INSERT ON phase1_physical_calls TO opencitadel_execution_kernel")
        )
        await db.commit()
    try:
        yield sessions, orchestrator
    finally:
        await engine.dispose()


def envelope(run, owner, name, payload, *, proof=None, command_id=None):
    return CommandEnvelope(
        command_id=command_id or uuid4(),
        command_type=name,
        command_schema_version=2
        if name
        in {
            "CompleteActivity",
            "FailActivity",
            "MarkActivityCallStarted",
            "MarkActivityOutcomeUnknown",
            "RequestActivity",
        }
        else 1,
        stream_type="run",
        stream_id=str(run),
        owner_user_id=owner,
        team_id=None,
        correlation_id=run,
        causation_id=None,
        issued_at=datetime.now(UTC),
        payload=payload,
        activity_lease_proof=proof,
    )


async def seed(runtime, *, count=1, expired=False, timeout_seconds=300):
    sessions, orchestrator = runtime
    run, owner = uuid4(), f"phase1-{uuid4()}"

    async def send(name, payload):
        result = await orchestrator.handle(envelope(run, owner, name, payload))
        assert result.status == "accepted", result

    await send(
        "CreateRun",
        {
            "family": "agent",
            "source_entity_type": "phase1-proof",
            "source_entity_id": str(run),
            "semantic_payload": {},
            "policy_snapshot": run_policy_snapshot_json("agent"),
        },
    )
    await send("StartRun", {})
    activities = [uuid4() for _ in range(count)]
    for index, activity in enumerate(activities):
        await send(
            "RequestActivity",
            {
                "activity_id": str(activity),
                "activity_type": "phase1.probe",
                "timeout_at": (
                    datetime.now(UTC)
                    + timedelta(
                        seconds=timeout_seconds[index]
                        if isinstance(timeout_seconds, tuple)
                        else timeout_seconds
                    )
                ).isoformat(),
                "input_digest": "a" * 64,
            },
        )
    if expired:
        async with execution_admin_session() as db:
            await db.execute(
                update(ExecutionActivityTaskORM).values(
                    timeout_at=func.clock_timestamp() - timedelta(seconds=1)
                )
            )
            await db.commit()
    await PostgresFormalProjector(session_factory=sessions, authorization=AUTH).run_once(
        OwnerScope.personal(owner), limit=1000, notify=False
    )
    return run, owner, activities


async def process(**options):
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.app.integration.phase1_worker_process",
        json.dumps(options),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(os.environ),
    )


async def outcome(child):
    try:
        stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=60)
    except TimeoutError:
        child.kill()
        await child.wait()
        raise
    assert child.returncode == 0, stderr.decode()
    return json.loads(stdout.decode().strip().splitlines()[-1])


async def physical_calls():
    async with execution_admin_session() as db:
        return (await db.execute(text("SELECT * FROM phase1_physical_calls"))).mappings().all()


async def test_two_workers_claim_only_reserved_slots(runtime):
    await seed(runtime, count=100)
    children = await asyncio.gather(process(worker="a", delay=0.1), process(worker="b", delay=0.1))
    peak = {"a": 0, "b": 0}
    finished = [asyncio.create_task(outcome(child)) for child in children]
    while not all(task.done() for task in finished):
        async with execution_admin_session() as db:
            counts = (
                await db.execute(
                    text(
                        "SELECT claimed_by,count(*) FROM execution_activity_tasks WHERE status IN ('claimed','call_started') GROUP BY claimed_by"
                    )
                )
            ).all()
        for worker, count in counts:
            peak[worker] = max(peak.get(worker, 0), count)
        await asyncio.sleep(0.02)
    results = await asyncio.gather(*finished)
    assert sum(result["stats"]["succeeded"] for result in results) == 100
    assert all(0 < value <= 8 for value in peak.values()), peak
    calls = await physical_calls()
    assert len(calls) == len({row["activity_id"] for row in calls}) == 100


async def test_expired_waiter_never_dispatches(runtime):
    await seed(runtime, expired=True)
    result = await outcome(await process(worker="expired", limit=1))
    assert result["stats"]["stale"] == 1
    assert await physical_calls() == []


async def test_waiter_deadline_expires_while_the_only_slot_is_busy(runtime):
    _, _, (first, second) = await seed(runtime, count=2, timeout_seconds=(30, 8))
    async with execution_admin_session() as db:
        before = await db.get(ExecutionActivityTaskORM, second)
        deadline = before.timeout_at
    result = await outcome(
        await process(worker="one-slot", slots=1, limit=2, hold_until=deadline.isoformat())
    )
    assert result["stats"]["succeeded"] == 1
    assert result["stats"]["stale"] == 1
    calls = await physical_calls()
    assert [row["activity_id"] for row in calls] == [first]
    assert calls[0]["sent_at"] < deadline
    async with execution_admin_session() as db:
        after = await db.get(ExecutionActivityTaskORM, second)
        assert after.timeout_at == deadline
        assert after.call_started_at is None
        assert after.attempt == 0
        assert after.claim_attempts == 1


@pytest.mark.parametrize("fault", ["false", "timeout"])
async def test_heartbeat_loss_fences_old_worker_and_replacement_never_resends(runtime, fault):
    run, owner, activities = await seed(runtime)
    old = await outcome(
        await process(worker="old", limit=1, ttl=3, delay=8, fault=fault, fault_after_send=True)
    )
    assert old["stats"]["stale"] == 1
    assert len(await physical_calls()) == 1
    async with execution_admin_session() as db:
        remaining = await db.scalar(
            text(
                "SELECT extract(epoch FROM claim_deadline-clock_timestamp()) FROM execution_activity_tasks WHERE activity_id=:id"
            ),
            {"id": activities[0]},
        )
    await asyncio.sleep(max(0, float(remaining)) + 0.05)
    replacement = await outcome(await process(worker="new", limit=1, ttl=10))
    assert replacement["stats"]["unknown"] == 1
    assert len(await physical_calls()) == 1
    proof = ActivityLeaseProof.model_validate(old["claims"][0])
    late = await runtime[1].handle(
        envelope(
            run,
            owner,
            "CompleteActivity",
            {
                "activity_id": str(activities[0]),
                "generation": 0,
                "claim_generation": proof.claim_generation,
                "result_summary": "late",
            },
            proof=proof,
        )
    )
    assert late.status == "rejected"
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ExecutionEventORM)
                .where(
                    ExecutionEventORM.stream_id == str(run),
                    ExecutionEventORM.event_type == "ActivityCompleted",
                )
            )
            == 0
        )


async def test_transient_heartbeat_recovers_same_claim(runtime):
    await seed(runtime)
    result = await outcome(
        await process(worker="recover", limit=1, ttl=2.4, delay=2, fault="transient")
    )
    assert result["stats"]["succeeded"] == 1
    assert result["renewals"] >= 2
    assert len(result["claims"]) == 1
    calls = await physical_calls()
    assert len(calls) == 1
    assert calls[0]["operation_key"] == result["claims"][0]["activity_id"]


async def test_late_settlement_fenced_after_transfer(runtime):
    run, owner, (activity,) = await seed(runtime)
    old = await process(worker="old", limit=1, ttl=3, delay=0.5, fault="timeout", retry_stale=True)
    replacement = None
    try:
        async with asyncio.timeout(15):
            while not await physical_calls():  # noqa: ASYNC110 - observe independent process through PostgreSQL
                await asyncio.sleep(0.01)
        async with execution_admin_session() as blocker:
            await blocker.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": PostgresEventStore._scope_advisory_lock_key(owner, None)},
            )
            async with asyncio.timeout(15):
                while True:
                    async with execution_admin_session() as db:
                        blocked = await db.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity JOIN pg_locks USING(pid) WHERE application_name='phase1-worker-old' AND locktype='advisory' AND NOT granted"
                            )
                        )
                    if blocked:
                        break
                    await asyncio.sleep(0.01)
            # The first worker's completion really reached the DB and is
            # blocked before the task lock. The lease is allowed to expire.
            await asyncio.sleep(3.2)
            replacement = await process(worker="new", limit=1, ttl=15)
            async with asyncio.timeout(15):
                while True:
                    async with execution_admin_session() as db:
                        task = await db.get(ExecutionActivityTaskORM, activity)
                        transferred = task.claimed_by == "new" and task.claim_generation == 2
                    if transferred:
                        break
                    await asyncio.sleep(0.01)
            assert len(await physical_calls()) == 1
            await blocker.commit()
        first, second = await asyncio.gather(outcome(old), outcome(replacement))
        assert first["stats"]["stale"] == 1
        assert first["late_status"] == "rejected"
        assert second["stats"]["unknown"] == 1
        assert first["claims"][0]["claim_generation"] == 1
        assert second["claims"][0]["claim_generation"] == 2
        assert len(await physical_calls()) == 1
        async with execution_admin_session() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(ExecutionEventORM)
                    .where(
                        ExecutionEventORM.stream_id == str(run),
                        ExecutionEventORM.event_type == "ActivityCompleted",
                    )
                )
                == 0
            )
    finally:
        for child in (old, replacement):
            if child is not None and child.returncode is None:
                child.kill()
                await child.wait()


@pytest.mark.parametrize(("started", "idempotent"), [(False, False), (True, True), (True, False)])
async def test_real_timer_dispatcher_settles_due_activity_with_durable_authority(
    runtime, started, idempotent
):
    sessions, _ = runtime
    run, owner, (activity,) = await seed(runtime, timeout_seconds=2)

    class Handler:
        activity_type = "phase1.probe"

    handler = Handler()
    handler.idempotent = idempotent
    orchestrator = SqlAlchemyExecutionOrchestrator(
        session_factory=sessions,
        aggregates={"run": RunAggregate()},
        authorization=AUTH,
        activity_timeout=PostgresActivityTimeoutGuard(create_activity_registry(handler)),
    )
    if started:
        store = PostgresActivityStore(session_factory=sessions, authorization=AUTH)
        (claim,) = await store.claim(
            now=datetime.now(UTC), limit=1, worker_id="timer-owner", claim_ttl=timedelta(seconds=30)
        )
        assert await store.mark_call_started(claim, now=datetime.now(UTC))
        assert (
            await orchestrator.handle(
                envelope(
                    run,
                    owner,
                    "MarkActivityCallStarted",
                    {
                        "activity_id": str(activity),
                        "generation": 0,
                        "claim_generation": claim.claim_generation,
                    },
                    proof=claim.lease_proof,
                )
            )
        ).status == "accepted"
    await asyncio.sleep(2.1)
    fired = await SqlAlchemyTimerDispatcher(session_factory=sessions, authorization=AUTH).fire_due(
        limit=100, now=datetime.now(UTC), claim_ttl=timedelta(seconds=30)
    )
    assert fired.fired == 1

    async def pending_command(kind):
        async with execution_admin_session() as db:
            row = await db.scalar(
                select(ExecutionCommandInboxORM).where(
                    ExecutionCommandInboxORM.stream_id == str(run),
                    ExecutionCommandInboxORM.command_type == kind,
                    ExecutionCommandInboxORM.status == "received",
                )
            )
            assert row is not None
            return PostgresInboxSource._to_command(row)

    timeout = await pending_command("FailActivity")
    assert timeout.activity_lease_proof is None
    result = await orchestrator.handle(timeout)
    unknown = started and not idempotent
    assert result.status == ("rejected" if unknown else "accepted")
    if unknown:
        forwarded = await pending_command("MarkActivityOutcomeUnknown")
        assert (await orchestrator.handle(forwarded)).status == "accepted"
    async with execution_admin_session() as db:
        task = await db.get(ExecutionActivityTaskORM, activity)
        assert task.status == ("unknown" if unknown else "failed")


async def test_committed_submission_response_loss(runtime):
    run, owner, (activity,) = await seed(runtime)
    store = PostgresActivityStore(session_factory=runtime[0], authorization=AUTH)
    (claim,) = await store.claim(
        now=datetime.now(UTC), limit=1, worker_id="a", claim_ttl=timedelta(minutes=1)
    )
    assert await store.mark_call_started(claim, now=datetime.now(UTC))
    proof = claim.lease_proof
    command = envelope(
        run,
        owner,
        "CompleteActivity",
        {
            "activity_id": str(activity),
            "generation": 0,
            "claim_generation": 1,
            "decision_data": {"visible": "one result"},
        },
        proof=proof,
    )
    first = await runtime[1].handle(command)
    assert first.status == "accepted"
    # The caller lost the response and retries the exact durable submission.
    second = await runtime[1].handle(command)
    assert first == second
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ExecutionEventORM)
                .where(
                    ExecutionEventORM.stream_id == str(run),
                    ExecutionEventORM.event_type == "ActivityCompleted",
                )
            )
            == 1
        )


async def test_deadline_advances_inside_accepting_transaction(runtime):
    run, owner, (activity,) = await seed(runtime)
    proof = ActivityLeaseProof(
        activity_id=activity, request_generation=0, claim_generation=1, worker_id="a"
    )
    async with execution_admin_session() as db:
        await db.execute(
            update(ExecutionActivityTaskORM).values(
                status="call_started",
                claim_generation=1,
                claimed_by="a",
                claim_deadline=func.clock_timestamp() + timedelta(seconds=0.4),
            )
        )
        await db.commit()
    command = envelope(
        run,
        owner,
        "CompleteActivity",
        {
            "activity_id": str(activity),
            "generation": 0,
            "claim_generation": 1,
        },
        proof=proof,
    )
    async with runtime[0]() as db:
        await configure_session_authorization(db, AUTH)
        assert await authorize_activity_command(db, command)
        await db.execute(text("SELECT pg_sleep(0.5)"))
        with pytest.raises(ActivityLeaseExpiredError):
            await ensure_activity_lease_current(db, command)
        await db.rollback()


async def test_no_proof_cannot_impersonate_timeout(runtime):
    run, owner, (activity,) = await seed(runtime)
    result = await runtime[1].handle(
        envelope(
            run,
            owner,
            "FailActivity",
            {
                "activity_id": str(activity),
                "generation": 0,
                "failure_code": "ACTIVITY_TIMEOUT",
            },
        )
    )
    assert result.status == "rejected"


@pytest.mark.parametrize("invalid", ["lease", "timeout", "worker", "capture_delay"])
async def test_content_capture_requires_current_lease_until_commit(runtime, invalid):
    run, owner, (activity,) = await seed(runtime)
    store = PostgresActivityStore(session_factory=runtime[0], authorization=AUTH)
    (claim,) = await store.claim(
        now=datetime.now(UTC),
        limit=1,
        worker_id="content-worker",
        claim_ttl=timedelta(seconds=2 if invalid == "capture_delay" else 30),
    )
    proof = claim.lease_proof
    async with execution_admin_session() as db:
        if invalid in {"lease", "timeout"}:
            values = {
                "claim_deadline" if invalid == "lease" else "timeout_at": func.clock_timestamp()
                - timedelta(seconds=1)
            }
            await db.execute(
                update(ExecutionActivityTaskORM)
                .where(ExecutionActivityTaskORM.activity_id == activity)
                .values(**values)
            )
        elif invalid == "worker":
            proof = proof.model_copy(update={"worker_id": "different-worker"})
        else:
            # Simulate slow materialization after authority was checked. The
            # final DB clock must reject and roll back the inserted content.
            await db.execute(
                text(
                    "CREATE FUNCTION phase1_slow_content() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(2.1); RETURN NEW; END $$"
                )
            )
            await db.execute(
                text(
                    "CREATE TRIGGER phase1_slow_content BEFORE INSERT ON execution_public_content FOR EACH ROW EXECUTE FUNCTION phase1_slow_content()"
                )
            )
        await db.commit()
    producer = ArtifactProducer(
        scope=OwnerScope.personal(owner),
        run_id=run,
        activity_id=activity,
        generation=0,
        claim_generation=claim.claim_generation,
    )
    writer = ExecutionContentWriter(session_factory=runtime[0], authorization=AUTH, objects=None)
    expected = (
        "snapshot claim expired during capture"
        if invalid == "capture_delay"
        else "snapshot claim unavailable"
    )
    with pytest.raises(ResourceUnavailable, match=expected):
        await writer.record(
            producer,
            command_id=uuid4(),
            phase="output",
            value={"answer": "late"},
            lease_proof=proof,
        )
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text("SELECT count(*) FROM execution_public_content WHERE activity_id=:activity"),
                {"activity": activity},
            )
            == 0
        )
