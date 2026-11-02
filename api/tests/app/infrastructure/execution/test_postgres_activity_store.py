"""Source and model contracts for PostgreSQL Activity lease fencing."""

import inspect

from app.infrastructure.execution.models import ExecutionActivityTaskORM
from app.infrastructure.execution.postgres_activity_store import (
    PostgresActivityStore,
)


def test_activity_model_separates_request_and_claim_generations() -> None:
    columns = ExecutionActivityTaskORM.__table__.columns

    assert "request_generation" in columns
    assert "claim_generation" in columns
    assert "generation" not in columns
    assert "call_started_at" in columns
    assert "completed_at" in columns


def test_claim_query_is_skip_locked_and_every_mutation_is_generation_fenced() -> None:
    source = inspect.getsource(PostgresActivityStore)
    normalized = " ".join(source.split())

    assert "with_for_update(skip_locked=True)" in source
    assert "ExecutionActivityTaskORM.claim_generation == claim.claim_generation" in normalized
    assert "recovered_after_call_started" in source
    assert source.count("await session.commit()") >= 4


# ---------------------------------------------------------------------------
# Real-PostgreSQL contracts: claim-attempt dead-lettering and retention purge.
# ---------------------------------------------------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402
from uuid import uuid4  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import delete, func, select, text, update  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.domain.models.authorization import AuthorizationContext  # noqa: E402
from app.infrastructure.execution.models import (  # noqa: E402
    ExecutionCommandInboxORM,
    ExecutionEventORM,
    ExecutionRunProjectionORM,
    ExecutionStreamOwnerORM,
)
from core.config import load_deployment_settings  # noqa: E402
from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: E402, F401
from tests.app.execution_database_fixture import isolated_execution_database  # noqa: E402, F401
from tests.app.execution_test_support import (  # noqa: E402
    authenticated_session_factory,
    execution_admin_session,
    execution_kernel_database_uri,
    run_policy_snapshot_json,
)

NOW = datetime(2026, 8, 27, 9, tzinfo=UTC)
OWNER = "activity-store-user"


@pytest.fixture(autouse=True)
def isolated_queue(request):
    if "_db_schema" in request.fixturenames:
        request.getfixturevalue("isolated_execution_database")


async def _seed_event(session, run_id) -> int:
    session.add(
        ExecutionStreamOwnerORM(
            stream_type="run",
            stream_id=str(run_id),
            owner_user_id=OWNER,
            team_id=None,
        )
    )
    await session.flush()
    event = ExecutionEventORM(
        event_id=uuid4(),
        stream_type="run",
        stream_id=str(run_id),
        stream_version=1,
        event_type="ActivityRequested",
        event_schema_version=1,
        public_payload={},
        internal_payload={},
        owner_user_id=OWNER,
        team_id=None,
        correlation_id=uuid4(),
        causation_id=None,
        occurred_at=NOW,
        prev_hash="0" * 64,
        event_hash="1" * 64,
    )
    session.add(event)
    await session.flush()
    return event.position


def _task_row(
    *,
    run_id,
    request_event_position: int,
    status: str = "pending",
    updated_at: datetime = NOW,
    claim_attempts: int = 0,
) -> ExecutionActivityTaskORM:
    return ExecutionActivityTaskORM(
        activity_id=uuid4(),
        run_id=str(run_id),
        aggregate_type="run",
        aggregate_id=str(run_id),
        activity_type="model.call",
        request_event_position=request_event_position,
        owner_user_id=OWNER,
        team_id=None,
        status=status,
        request_generation=0,
        claim_attempts=claim_attempts,
        available_at=NOW - timedelta(hours=1),
        timeout_at=datetime.now(UTC) + timedelta(hours=1),
        request_ref=None,
        request_digest="a" * 64,
        request_payload={},
        created_at=updated_at,
        updated_at=updated_at,
    )


async def _cleanup(run_ids, activity_ids) -> None:
    async with execution_admin_session() as session:
        await session.execute(
            delete(ExecutionActivityTaskORM).where(
                ExecutionActivityTaskORM.activity_id.in_(activity_ids)
            )
        )
        await session.execute(
            delete(ExecutionRunProjectionORM).where(ExecutionRunProjectionORM.run_id.in_(run_ids))
        )
        stream_ids = [str(run_id) for run_id in run_ids]
        await session.execute(
            delete(ExecutionCommandInboxORM).where(
                ExecutionCommandInboxORM.stream_id.in_(stream_ids)
            )
        )
        await session.execute(
            text("ALTER TABLE execution_events DISABLE TRIGGER execution_events_immutable")
        )
        await session.execute(
            text(
                "DELETE FROM execution_events WHERE stream_type = 'run' AND stream_id = ANY(:ids)"
            ),
            {"ids": stream_ids},
        )
        await session.execute(
            text("ALTER TABLE execution_events ENABLE TRIGGER execution_events_immutable")
        )
        await session.execute(
            text(
                "ALTER TABLE execution_stream_owners "
                "DISABLE TRIGGER execution_stream_owners_immutable"
            )
        )
        await session.execute(
            text(
                "DELETE FROM execution_stream_owners "
                "WHERE stream_type = 'run' AND stream_id = ANY(:ids)"
            ),
            {"ids": stream_ids},
        )
        await session.execute(
            text(
                "ALTER TABLE execution_stream_owners "
                "ENABLE TRIGGER execution_stream_owners_immutable"
            )
        )
        await session.commit()


def _projection_row(run_id, *, terminal: bool) -> ExecutionRunProjectionORM:
    snapshot = run_policy_snapshot_json("agent")
    return ExecutionRunProjectionORM(
        run_id=run_id,
        family="agent",
        source_entity_type="session",
        source_entity_id=str(run_id),
        execution_policy_revision_id=snapshot["execution_revision_id"],
        execution_policy_digest=snapshot["execution_policy_digest"],
        status="completed" if terminal else "running",
        terminal=terminal,
        wait_reason=None,
        active_activity_count=0,
        decision_due_at=None,
        parent_run_id=None,
        correlation_id=uuid4(),
        owner_user_id=OWNER,
        team_id=None,
        stream_version=3,
        last_event_position=3,
        state={},
        state_hash="0" * 64,
        last_event_hash="0" * 64,
        created_at=NOW,
        updated_at=NOW,
        terminal_at=NOW if terminal else None,
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_claim_cap_parks_poison_activity_as_dead_lettered() -> None:
    """K2-2: an activity claimed past the cap stops being reclaimed, and a
    FailActivity(ACTIVITY_DEAD_LETTERED) command is written into the inbox in
    the same transaction so the Run converges promptly (the activity-timeout
    timer stays as the crash backstop)."""
    run_id = uuid4()
    async with execution_admin_session() as session:
        position = await _seed_event(session, run_id)
        row = _task_row(run_id=run_id, request_event_position=position)
        activity_id = row.activity_id
        session.add(row)
        await session.commit()

    engine = create_async_engine(execution_kernel_database_uri())
    sessions = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    store = PostgresActivityStore(
        session_factory=sessions,
        authorization=AuthorizationContext.system("activity-deadletter-test"),
        max_claim_attempts=2,
    )
    try:
        for round_index in range(2):
            claims = await store.claim(
                now=NOW + timedelta(seconds=round_index * 10),
                limit=10,
                worker_id="worker-1",
                claim_ttl=timedelta(seconds=30),
            )
            assert len(claims) == 1
            assert await store.defer(
                claims[0],
                now=NOW + timedelta(seconds=round_index * 10),
                retry_after=timedelta(seconds=1),
                consume_attempt=True,
            )
            # Advance only this test-owned row's availability; caller `now`
            # deliberately cannot move the database lease clock any more.
            async with execution_admin_session() as session:
                await session.execute(
                    update(ExecutionActivityTaskORM)
                    .where(ExecutionActivityTaskORM.activity_id == activity_id)
                    .values(available_at=func.clock_timestamp() - timedelta(seconds=1))
                )
                await session.commit()

        third = await store.claim(
            now=NOW + timedelta(minutes=5),
            limit=10,
            worker_id="worker-1",
            claim_ttl=timedelta(seconds=30),
        )
        assert third == ()

        async with execution_admin_session() as session:
            record = await session.get(ExecutionActivityTaskORM, activity_id)
            assert record is not None
            assert record.status == "dead_lettered"
            assert record.failure_code == "ACTIVITY_DEAD_LETTERED"
            assert record.claim_attempts == 3
            # Prompt convergence: the settle command is already durable.
            settle = await session.scalar(
                select(ExecutionCommandInboxORM).where(
                    ExecutionCommandInboxORM.stream_id == str(run_id),
                    ExecutionCommandInboxORM.command_type == "FailActivity",
                )
            )
            assert settle is not None
            assert settle.payload["failure_code"] == "ACTIVITY_DEAD_LETTERED"
            assert settle.payload["activity_id"] == str(activity_id)

        # Dead-lettered rows are out of the claim scan for good.
        assert (
            await store.claim(
                now=NOW + timedelta(minutes=10),
                limit=10,
                worker_id="worker-1",
                claim_ttl=timedelta(seconds=30),
            )
            == ()
        )
    finally:
        await engine.dispose()
        await _cleanup([run_id], [activity_id])


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
@pytest.mark.parametrize(
    "effect",
    [
        {"state": "unknown", "abandoned": True},
        {"state": "applied", "awaiting_result": True},
        {"state": "not_applied", "integrity_conflict": True},
    ],
)
async def test_purge_keeps_settled_activities_of_active_runs(effect) -> None:
    """K2-5 hard constraint: settled activity rows are purged only once their
    owning Run is terminal — an active Run's decision hydration depends on
    the decision_payload column of these rows."""
    terminal_run = uuid4()
    active_run = uuid4()
    unsettled_run = uuid4()
    async with execution_admin_session() as session:
        terminal_position = await _seed_event(session, terminal_run)
        active_position = await _seed_event(session, active_run)
        unsettled_position = await _seed_event(session, unsettled_run)
        session.add(_projection_row(terminal_run, terminal=True))
        session.add(_projection_row(active_run, terminal=False))
        unsettled_projection = _projection_row(unsettled_run, terminal=True)
        unsettled_projection.state = {**unsettled_projection.state, "effect_operations": [effect]}
        session.add(unsettled_projection)
        old_terminal_row = _task_row(
            run_id=terminal_run,
            request_event_position=terminal_position,
            status="succeeded",
            updated_at=NOW - timedelta(days=60),
        )
        fresh_terminal_row = _task_row(
            run_id=terminal_run,
            request_event_position=terminal_position,
            status="succeeded",
            updated_at=NOW - timedelta(hours=1),
        )
        old_active_row = _task_row(
            run_id=active_run,
            request_event_position=active_position,
            status="succeeded",
            updated_at=NOW - timedelta(days=60),
        )
        old_terminal_id = old_terminal_row.activity_id
        old_unsettled_row = _task_row(
            run_id=unsettled_run,
            request_event_position=unsettled_position,
            status="unknown",
            updated_at=NOW - timedelta(days=60),
        )
        old_unsettled_id = old_unsettled_row.activity_id
        fresh_terminal_id = fresh_terminal_row.activity_id
        old_active_id = old_active_row.activity_id
        ids = [old_terminal_id, fresh_terminal_id, old_active_id, old_unsettled_id]
        session.add_all([old_terminal_row, fresh_terminal_row, old_active_row, old_unsettled_row])
        await session.commit()

    engine = create_async_engine(execution_kernel_database_uri())
    sessions = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    store = PostgresActivityStore(
        session_factory=sessions,
        authorization=AuthorizationContext.system("activity-purge-test"),
    )
    try:
        purged = await store.purge_completed(before=NOW - timedelta(days=30), limit=100)
        assert purged == 1

        async with execution_admin_session() as session:
            remaining = set(
                (
                    await session.scalars(
                        select(ExecutionActivityTaskORM.activity_id).where(
                            ExecutionActivityTaskORM.activity_id.in_(ids)
                        )
                    )
                ).all()
            )
        # Only the terminal Run's old row is gone; the active Run's old row and
        # the terminal Run's fresh row survive.
        assert remaining == {fresh_terminal_id, old_active_id, old_unsettled_id}
    finally:
        await engine.dispose()
        await _cleanup([terminal_run, active_run, unsettled_run], ids)


@pytest.fixture
async def owned_lease_store(_db_schema):
    """Requires the caller's registered isolated test DB; never selects user rows."""
    run_id = uuid4()
    async with execution_admin_session() as session:
        position = await _seed_event(session, run_id)
        row = _task_row(run_id=run_id, request_event_position=position)
        row.available_at = datetime.now(UTC) - timedelta(minutes=1)
        row.timeout_at = datetime.now(UTC) + timedelta(minutes=5)
        activity_id = row.activity_id
        session.add(row)
        await session.commit()
    engine = create_async_engine(execution_kernel_database_uri())
    sessions = authenticated_session_factory(
        engine, signing_secret=load_deployment_settings().session_secret
    )
    store = PostgresActivityStore(
        session_factory=sessions,
        authorization=AuthorizationContext.system("phase1-lease-store-test"),
    )
    try:
        yield store, activity_id
    finally:
        await engine.dispose()
        await _cleanup([run_id], [activity_id])


@pytest.mark.asyncio
async def test_claim_and_renewal_use_database_clock_despite_caller_clock_skew(owned_lease_store):
    store, activity_id = owned_lease_store
    before = datetime.now(UTC)
    (claimed,) = await store.claim(
        now=NOW - timedelta(days=365),
        limit=1,
        worker_id="worker-clock",
        claim_ttl=timedelta(seconds=10),
    )
    assert claimed.request.activity_id == activity_id
    assert before - timedelta(seconds=1) <= claimed.server_now <= datetime.now(UTC)
    assert claimed.lease_deadline - claimed.server_now == timedelta(seconds=10)
    confirmation = await store.heartbeat(
        claimed, now=NOW + timedelta(days=36500), claim_ttl=timedelta(seconds=10)
    )
    assert confirmation is not None
    assert confirmation.lease_deadline - confirmation.server_now == timedelta(seconds=10)
    assert confirmation.server_now < before + timedelta(seconds=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["worker_id", "claim_generation", "request_generation"])
async def test_all_lease_mutations_reject_wrong_identity(owned_lease_store, field):
    store, _ = owned_lease_store
    (claimed,) = await store.claim(
        now=NOW, limit=1, worker_id="worker-owner", claim_ttl=timedelta(seconds=10)
    )
    if field == "request_generation":
        bad = claimed.model_copy(
            update={"request": claimed.request.model_copy(update={"generation": 1})}
        )
    else:
        bad = claimed.model_copy(update={field: "worker-other" if field == "worker_id" else 99})
    assert not await store.mark_call_started(bad, now=NOW)
    assert await store.heartbeat(bad, now=NOW, claim_ttl=timedelta(seconds=10)) is None
    assert not await store.defer(bad, now=NOW, retry_after=timedelta(seconds=1))
    assert await store.mark_call_started(claimed, now=NOW)


@pytest.mark.asyncio
async def test_lock_wait_past_deadline_cannot_renew_expired_claim(owned_lease_store):
    import asyncio

    store, activity_id = owned_lease_store
    (claimed,) = await store.claim(
        now=NOW, limit=1, worker_id="worker-a", claim_ttl=timedelta(seconds=1)
    )
    async with execution_admin_session() as locker:
        await locker.scalar(
            select(ExecutionActivityTaskORM)
            .where(ExecutionActivityTaskORM.activity_id == activity_id)
            .with_for_update()
        )
        renewing = asyncio.create_task(
            store.heartbeat(claimed, now=NOW, claim_ttl=timedelta(seconds=10))
        )
        assert datetime.now(UTC) < claimed.lease_deadline
        await asyncio.sleep(1.15)
        await locker.commit()
    assert await asyncio.wait_for(renewing, timeout=2) is None
    assert not await store.mark_call_started(claimed, now=NOW)
    assert await store.heartbeat(claimed, now=NOW, claim_ttl=timedelta(seconds=10)) is None
    assert not await store.defer(claimed, now=NOW, retry_after=timedelta(seconds=1))
    (replacement,) = await store.claim(
        now=NOW, limit=1, worker_id="worker-b", claim_ttl=timedelta(seconds=10)
    )
    assert replacement.claim_generation == claimed.claim_generation + 1
    assert replacement.worker_id == "worker-b"
    assert await store.mark_call_started(replacement, now=NOW)


@pytest.mark.asyncio
async def test_expired_business_deadline_cannot_mark_external_call_started(owned_lease_store):
    store, activity_id = owned_lease_store
    async with execution_admin_session() as session:
        await session.execute(
            update(ExecutionActivityTaskORM)
            .where(ExecutionActivityTaskORM.activity_id == activity_id)
            .values(timeout_at=func.clock_timestamp() - timedelta(seconds=1))
        )
        await session.commit()
    (claimed,) = await store.claim(
        now=NOW, limit=1, worker_id="worker-expired", claim_ttl=timedelta(seconds=10)
    )
    assert not await store.mark_call_started(claimed, now=NOW)
    assert await store.heartbeat(claimed, now=NOW, claim_ttl=timedelta(seconds=10)) is None
    assert not await store.defer(claimed, now=NOW, retry_after=timedelta(seconds=1))


@pytest.mark.asyncio
async def test_expected_defer_refunds_claim_attempt_but_retains_started_evidence(owned_lease_store):
    store, activity_id = owned_lease_store
    (claimed,) = await store.claim(
        now=NOW, limit=1, worker_id="worker-deferred", claim_ttl=timedelta(seconds=10)
    )
    assert await store.mark_call_started(claimed, now=NOW)
    assert await store.defer(claimed, now=NOW, retry_after=timedelta(seconds=1))
    async with execution_admin_session() as session:
        record = await session.get(ExecutionActivityTaskORM, activity_id)
        assert record is not None
        assert record.claim_attempts == 0
        assert record.attempt == 1
        assert record.call_started_at is not None
        assert record.status == "pending"


@pytest.mark.asyncio
async def test_claim_cap_cannot_fabricate_failure_for_a_possibly_sent_operation(owned_lease_store):
    store, activity_id = owned_lease_store
    async with execution_admin_session() as session:
        await session.execute(
            update(ExecutionActivityTaskORM)
            .where(ExecutionActivityTaskORM.activity_id == activity_id)
            .values(
                claim_attempts=100,
                call_started_at=func.clock_timestamp() - timedelta(seconds=10),
            )
        )
        await session.commit()
    (claimed,) = await store.claim(
        now=NOW, limit=1, worker_id="worker-recover", claim_ttl=timedelta(seconds=10)
    )
    assert claimed.recovered_after_call_started
    assert claimed.request.activity_id == activity_id
