from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.execution.aggregate import ReplaySnapshot, replay
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.errors import CommandInProgressError
from app.domain.execution.events import NewEvent
from app.domain.execution.registry import UnregisteredSchemaError
from app.domain.execution.run import RunAggregate
from app.domain.execution.store import (
    AppendContext,
    CorruptEventStreamError,
    OptimisticConcurrencyError,
    StreamRef,
)
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import (
    ExecutionActivityTaskORM,
    ExecutionCommandInboxORM,
    ExecutionEventORM,
    ExecutionOutboxORM,
    ExecutionScheduledCommandORM,
    ExecutionSnapshotORM,
)
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.execution.postgres_inbox import PostgresInbox
from app.infrastructure.execution.postgres_snapshot_store import PostgresSnapshotStore
from app.infrastructure.execution.postgres_timer_store import PostgresTimerStore
from app.infrastructure.execution.sqlalchemy_orchestrator import (
    SqlAlchemyExecutionOrchestrator,
)
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_session,
    execution_kernel_database_uri,
    run_policy_snapshot_json,
)

NOW = datetime(2026, 8, 20, 13, 30, tzinfo=UTC)


def metric_sample(name: str, labels: dict | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


def command(
    command_type: str,
    *,
    stream_id: str,
    command_id=None,
    expected_version: int | None = None,
    payload: dict | None = None,
) -> CommandEnvelope:
    if payload is None and command_type == "CreateRun":
        payload = {
            "family": "agent",
            "source_entity_type": "session",
            "source_entity_id": stream_id,
            "semantic_payload": {},
            "public_input": {},
        }
    if command_type == "CreateRun" and payload is not None and "family" in payload:
        payload = dict(payload)
        payload.setdefault("policy_snapshot", run_policy_snapshot_json(payload["family"]))
    return CommandEnvelope(
        command_id=command_id or uuid4(),
        command_type=command_type,
        command_schema_version=1,
        stream_type="run",
        stream_id=stream_id,
        expected_stream_version=expected_version,
        owner_user_id="orchestrator-user",
        team_id=None,
        correlation_id=uuid4(),
        causation_id=None,
        issued_at=NOW,
        payload=payload or {},
    )


async def cleanup(session_factory, *, stream_ids: list[str], command_ids: list) -> None:
    async with execution_admin_session() as session:
        await session.execute(
            delete(ExecutionActivityTaskORM).where(
                ExecutionActivityTaskORM.aggregate_id.in_(stream_ids)
            )
        )
        positions = select(ExecutionEventORM.position).where(
            ExecutionEventORM.stream_id.in_(stream_ids)
        )
        await session.execute(
            delete(ExecutionOutboxORM).where(ExecutionOutboxORM.event_position.in_(positions))
        )
        await session.execute(
            delete(ExecutionCommandInboxORM).where(
                ExecutionCommandInboxORM.command_id.in_(command_ids)
            )
        )
        await session.execute(
            delete(ExecutionSnapshotORM).where(ExecutionSnapshotORM.stream_id.in_(stream_ids))
        )
        await session.execute(
            delete(ExecutionScheduledCommandORM).where(
                ExecutionScheduledCommandORM.command_envelope["stream_id"].astext.in_(stream_ids)
            )
        )
        await session.execute(
            text("ALTER TABLE execution_events DISABLE TRIGGER execution_events_immutable")
        )
        await session.execute(
            delete(ExecutionEventORM).where(ExecutionEventORM.stream_id.in_(stream_ids))
        )
        await session.execute(
            text("ALTER TABLE execution_events ENABLE TRIGGER execution_events_immutable")
        )
        await session.commit()


@pytest.fixture
async def orchestrator_database(_db_schema):
    engine = create_async_engine(execution_kernel_database_uri())
    session_factory = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    stream_ids: list[str] = []
    command_ids: list = []
    try:
        yield session_factory, stream_ids, command_ids
    finally:
        await cleanup(
            session_factory,
            stream_ids=stream_ids,
            command_ids=command_ids,
        )
        await engine.dispose()


def make_orchestrator(session_factory, **overrides) -> SqlAlchemyExecutionOrchestrator:
    return SqlAlchemyExecutionOrchestrator(
        session_factory=session_factory,
        aggregates={"run": RunAggregate()},
        authorization=AuthorizationContext.system("orchestrator-test"),
        **overrides,
    )


@pytest.mark.asyncio
async def test_duplicate_accepted_command_returns_persisted_result_and_one_event(
    orchestrator_database,
) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    candidate = command("CreateRun", stream_id=stream_id)
    stream_ids.append(stream_id)
    command_ids.append(candidate.command_id)
    orchestrator = make_orchestrator(session_factory)

    first = await orchestrator.handle(candidate)
    duplicate = await orchestrator.handle(candidate)

    assert duplicate == first
    assert first.status == "accepted"
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-verify"),
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExecutionEventORM)
                .where(ExecutionEventORM.stream_id == stream_id)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_duplicate_business_rejection_is_persisted_without_events(
    orchestrator_database,
) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    candidate = command("StartRun", stream_id=stream_id)
    stream_ids.append(stream_id)
    command_ids.append(candidate.command_id)
    orchestrator = make_orchestrator(session_factory)

    first = await orchestrator.handle(candidate)
    duplicate = await orchestrator.handle(candidate)

    assert duplicate == first
    assert first.status == "rejected"
    assert first.rejection_code == "INVALID_TRANSITION"
    assert first.first_event_position is None


@pytest.mark.asyncio
async def test_unknown_command_is_rejected_with_a_stable_public_code(
    orchestrator_database,
) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    candidate = command("InventSyntheticState", stream_id=stream_id)
    stream_ids.append(stream_id)
    command_ids.append(candidate.command_id)

    result = await make_orchestrator(session_factory).handle(candidate)

    assert result.status == "rejected"
    assert result.rejection_code == "UNKNOWN_COMMAND"


@pytest.mark.asyncio
async def test_invalid_latest_command_schema_is_durably_rejected(
    orchestrator_database,
) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    candidate = command(
        "CreateRun",
        stream_id=stream_id,
        payload={"unexpected": True},
    )
    stream_ids.append(stream_id)
    command_ids.append(candidate.command_id)
    orchestrator = make_orchestrator(session_factory)

    first = await orchestrator.handle(candidate)
    duplicate = await orchestrator.handle(candidate)

    assert duplicate == first
    assert first.status == "rejected"
    assert first.rejection_code == "INVALID_COMMAND_SCHEMA"


@pytest.mark.asyncio
async def test_oversized_command_is_stored_as_digest_only_and_durably_rejected(
    orchestrator_database,
) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    candidate = command(
        "CreateRun",
        stream_id=stream_id,
        payload={"content": "x" * (64 * 1024)},
    )
    stream_ids.append(stream_id)
    command_ids.append(candidate.command_id)
    orchestrator = make_orchestrator(session_factory)

    first = await orchestrator.handle(candidate)
    duplicate = await orchestrator.handle(candidate)

    assert duplicate == first
    assert first.status == "rejected"
    assert first.rejection_code == "PAYLOAD_TOO_LARGE"
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-large-command-check"),
        )
        record = await session.get(ExecutionCommandInboxORM, candidate.command_id)
        assert record is not None
        assert record.payload == {}
        assert record.payload_ref is None
        assert record.payload_digest is not None
        assert record.last_error_code == "PAYLOAD_TOO_LARGE"


@pytest.mark.asyncio
async def test_invalid_historical_event_schema_fails_closed_before_decision(
    orchestrator_database,
) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    candidate = command("StartRun", stream_id=stream_id)
    stream_ids.append(stream_id)
    command_ids.append(candidate.command_id)
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-event-schema-seed"),
        )
        await PostgresEventStore(session).append(
            StreamRef(stream_type="run", stream_id=stream_id),
            0,
            (
                # A schema version the registry does not know (only v1 is
                # registered) must fail closed at the read boundary before
                # decide or append — as a ValueError the handler rolls back on,
                # never as a bare KeyError escaping the control plane.
                NewEvent(
                    event_type="RunCreated",
                    event_schema_version=2,
                    public_payload={"unexpected": True},
                    internal_payload={},
                ),
            ),
            AppendContext(
                owner_user_id="orchestrator-user",
                team_id=None,
                correlation_id=uuid4(),
                causation_id=uuid4(),
                occurred_at=NOW,
            ),
        )
        await session.commit()

    with pytest.raises(UnregisteredSchemaError, match="Unknown RunCreated version 2"):
        await make_orchestrator(session_factory).handle(candidate)

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-event-schema-check"),
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExecutionEventORM)
                .where(ExecutionEventORM.stream_id == stream_id)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_crash_after_inbox_receive_is_recovered_by_orchestrator(
    orchestrator_database,
) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    candidate = command("CreateRun", stream_id=stream_id)
    stream_ids.append(stream_id)
    command_ids.append(candidate.command_id)

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-receive-seed"),
        )
        assert await PostgresInbox(session).receive(candidate) is True
        await session.commit()

    result = await make_orchestrator(session_factory).handle(candidate)

    assert result.status == "accepted"


@pytest.mark.asyncio
async def test_command_in_progress_is_reported_as_deferred_and_not_fatal(
    orchestrator_database,
) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    candidate = command("CreateRun", stream_id=stream_id)
    stream_ids.append(stream_id)
    command_ids.append(candidate.command_id)

    class InProgressInbox(PostgresInbox):
        async def claim(self, command, *, now, claim_ttl):
            del command, now, claim_ttl
            raise CommandInProgressError("held by a concurrent worker")

    result = await make_orchestrator(
        session_factory,
        inbox_factory=InProgressInbox,
    ).handle(candidate)

    # Non-fatal: reported as deferred (retry later), nothing persisted.
    assert result.status == "deferred"
    assert result.rejection_code is None
    assert result.first_event_position is None
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-deferred-verify"),
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExecutionEventORM)
                .where(ExecutionEventORM.stream_id == stream_id)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_optimistic_conflict_reloads_and_redecides(orchestrator_database) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    candidate = command("CreateRun", stream_id=stream_id)
    stream_ids.append(stream_id)
    command_ids.append(candidate.command_id)
    append_attempts = 0

    class ConflictOnceStore(PostgresEventStore):
        async def append(self, stream, expected_version, events, context):
            nonlocal append_attempts
            append_attempts += 1
            if append_attempts == 1:
                raise OptimisticConcurrencyError(
                    expected_version=expected_version,
                    actual_version=expected_version,
                )
            return await super().append(stream, expected_version, events, context)

    before_conflicts = metric_sample("execution_optimistic_conflicts_total")
    result = await make_orchestrator(
        session_factory,
        event_store_factory=ConflictOnceStore,
    ).handle(candidate)

    assert result.status == "accepted"
    assert append_attempts == 2
    assert metric_sample("execution_optimistic_conflicts_total") - before_conflicts == 1


@pytest.mark.asyncio
async def test_inbox_result_events_and_outbox_are_one_transaction(
    orchestrator_database,
) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    candidate = command("CreateRun", stream_id=stream_id)
    stream_ids.append(stream_id)
    command_ids.append(candidate.command_id)

    class FailingInbox(PostgresInbox):
        async def complete(self, result, *, now):
            del result, now
            raise RuntimeError("injected failure before inbox result")

    with pytest.raises(RuntimeError, match="injected failure"):
        await make_orchestrator(
            session_factory,
            inbox_factory=FailingInbox,
        ).handle(candidate)

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-atomic-verify"),
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExecutionCommandInboxORM)
                .where(ExecutionCommandInboxORM.command_id == candidate.command_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExecutionEventORM)
                .where(ExecutionEventORM.stream_id == stream_id)
            )
            == 0
        )
        event_positions = select(ExecutionEventORM.position).where(
            ExecutionEventORM.stream_id == stream_id
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExecutionOutboxORM)
                .where(ExecutionOutboxORM.event_position.in_(event_positions))
            )
            == 0
        )


@pytest.mark.asyncio
async def test_activity_completion_event_and_operational_task_are_one_transaction(
    orchestrator_database,
) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    activity_id = uuid4()
    commands = [
        command("CreateRun", stream_id=stream_id),
        command("StartRun", stream_id=stream_id),
        command(
            "RequestActivity",
            stream_id=stream_id,
            payload={
                "activity_id": str(activity_id),
                "activity_type": "model.call",
                "timeout_at": (NOW + timedelta(minutes=5)).isoformat(),
                "input_digest": "a" * 64,
            },
        ),
        command(
            "CompleteActivity",
            stream_id=stream_id,
            payload={
                "activity_id": str(activity_id),
                "generation": 0,
                "result_ref": "object://activity-result",
                "result_summary": "completed",
            },
        ),
    ]
    stream_ids.append(stream_id)
    command_ids.extend(item.command_id for item in commands)
    orchestrator = make_orchestrator(session_factory, now=lambda: NOW)

    for item in commands[:-1]:
        assert (await orchestrator.handle(item)).status == "accepted"
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-activity-claim"),
        )
        task = await session.get(ExecutionActivityTaskORM, activity_id)
        assert task is not None
        task.status = "call_started"
        task.claim_generation = 1
        task.claimed_by = "worker-before-crash"
        task.claim_deadline = NOW + timedelta(seconds=30)
        task.call_started_at = NOW
        await session.commit()

    result = await orchestrator.handle(commands[-1])

    assert result.status == "accepted"
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-activity-verify"),
        )
        task = await session.get(ExecutionActivityTaskORM, activity_id)
        timer = await session.get(
            ExecutionScheduledCommandORM,
            uuid5(
                NAMESPACE_URL,
                f"opencitadel:activity-timeout:{activity_id}:0",
            ),
        )
        assert task is not None
        assert timer is not None
        assert task.status == "succeeded"
        assert task.result_ref == "object://activity-result"
        assert task.result_summary == "completed"
        assert task.completed_at == NOW
        assert task.claimed_by is None
        assert task.claim_deadline is None
        assert timer.status == "cancelled"
        assert timer.cancellation_activity_id == activity_id
        assert timer.cancellation_event_id is not None


@pytest.mark.asyncio
async def test_corrupt_stream_fails_closed_before_decide_or_append(
    orchestrator_database,
) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    request = command("CreateRun", stream_id=stream_id)
    start = command("StartRun", stream_id=stream_id)
    stream_ids.append(stream_id)
    command_ids.extend((request.command_id, start.command_id))
    orchestrator = make_orchestrator(session_factory)

    assert (await orchestrator.handle(request)).status == "accepted"
    async with execution_admin_session() as session:
        await session.execute(
            text("ALTER TABLE execution_events DISABLE TRIGGER execution_events_immutable")
        )
        await session.execute(
            text(
                "UPDATE execution_events SET public_payload = "
                "'{\"tampered\": true}'::jsonb "
                "WHERE stream_type = 'run' AND stream_id = :stream_id"
            ),
            {"stream_id": stream_id},
        )
        await session.execute(
            text("ALTER TABLE execution_events ENABLE TRIGGER execution_events_immutable")
        )
        await session.commit()

    with pytest.raises(CorruptEventStreamError):
        await orchestrator.handle(start)

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-corrupt-verify"),
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExecutionEventORM)
                .where(ExecutionEventORM.stream_id == stream_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExecutionCommandInboxORM)
                .where(ExecutionCommandInboxORM.command_id == start.command_id)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_command_replay_loads_valid_snapshot_and_only_its_event_tail(
    orchestrator_database,
) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    request = command("CreateRun", stream_id=stream_id)
    start = command("StartRun", stream_id=stream_id)
    complete = command("CompleteRun", stream_id=stream_id)
    stream_ids.append(stream_id)
    command_ids.extend((request.command_id, start.command_id, complete.command_id))
    orchestrator = make_orchestrator(session_factory)
    await orchestrator.handle(request)
    await orchestrator.handle(start)

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-snapshot-save"),
        )
        events = await PostgresEventStore(session).load_stream(
            "run",
            stream_id,
        )
        prefix = replay(RunAggregate(), events[:1], stream_id=stream_id)
        await PostgresSnapshotStore(session).save(
            "run",
            ReplaySnapshot(
                stream_id=stream_id,
                stream_version=prefix.stream_version,
                state=prefix.state,
                state_hash=prefix.state_hash,
                last_event_hash=prefix.last_event_hash,
            ),
            owner_user_id="orchestrator-user",
            team_id=None,
            serializer_version=RunAggregate.snapshot_serializer_version,
        )
        await session.commit()

    loads: list[tuple[int, str | None]] = []

    class RecordingStore(PostgresEventStore):
        async def load_stream(
            self,
            stream_type,
            stream_id,
            *,
            after_version=0,
            expected_previous_hash=None,
        ):
            loads.append((after_version, expected_previous_hash))
            return await super().load_stream(
                stream_type,
                stream_id,
                after_version=after_version,
                expected_previous_hash=expected_previous_hash,
            )

    result = await make_orchestrator(
        session_factory,
        event_store_factory=RecordingStore,
    ).handle(complete)

    assert result.status == "accepted"
    assert loads == [(1, events[0].event_hash)]
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-snapshot-terminal"),
        )
        terminal_snapshot = await session.scalar(
            select(ExecutionSnapshotORM).where(
                ExecutionSnapshotORM.stream_type == "run",
                ExecutionSnapshotORM.stream_id == stream_id,
                ExecutionSnapshotORM.stream_version == 3,
            )
        )
        assert terminal_snapshot is not None
        assert terminal_snapshot.state["status"] == "completed"


@pytest.mark.asyncio
async def test_corrupt_snapshot_is_deleted_and_command_falls_back_to_full_replay(
    orchestrator_database,
) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    request = command("CreateRun", stream_id=stream_id)
    start = command("StartRun", stream_id=stream_id)
    stream_ids.append(stream_id)
    command_ids.extend((request.command_id, start.command_id))
    await make_orchestrator(session_factory).handle(request)

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-snapshot-corrupt"),
        )
        events = await PostgresEventStore(session).load_stream(
            "run",
            stream_id,
        )
        prefix = replay(RunAggregate(), events, stream_id=stream_id)
        await PostgresSnapshotStore(session).save(
            "run",
            ReplaySnapshot(
                stream_id=stream_id,
                stream_version=prefix.stream_version,
                state=prefix.state,
                state_hash=prefix.state_hash,
                last_event_hash=prefix.last_event_hash,
            ),
            owner_user_id="orchestrator-user",
            team_id=None,
            serializer_version=RunAggregate.snapshot_serializer_version,
        )
        await session.flush()
        snapshot = await session.scalar(
            select(ExecutionSnapshotORM).where(ExecutionSnapshotORM.stream_id == stream_id)
        )
        assert snapshot is not None
        snapshot.state = {"stream_id": stream_id, "status": "failed"}
        await session.commit()

    result = await make_orchestrator(session_factory).handle(start)

    assert result.status == "accepted"
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-snapshot-check"),
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExecutionSnapshotORM)
                .where(ExecutionSnapshotORM.stream_id == stream_id)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_appended_cancellation_event_atomically_cancels_matching_timer(
    orchestrator_database,
) -> None:
    session_factory, stream_ids, command_ids = orchestrator_database
    stream_id = str(uuid4())
    request = command("CreateRun", stream_id=stream_id)
    start = command("StartRun", stream_id=stream_id)
    complete = command("CompleteRun", stream_id=stream_id)
    scheduled = command("CancelRun", stream_id=stream_id)
    timer_id = uuid4()
    deterministic_command_id = uuid5(
        NAMESPACE_URL,
        f"opencitadel:timer:{timer_id}",
    )
    stream_ids.append(stream_id)
    command_ids.extend(
        (
            request.command_id,
            start.command_id,
            complete.command_id,
            deterministic_command_id,
        )
    )
    orchestrator = make_orchestrator(session_factory)
    await orchestrator.handle(request)
    await orchestrator.handle(start)
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-timer-cancel-seed"),
        )
        session.add(
            ExecutionScheduledCommandORM(
                timer_id=timer_id,
                due_at=datetime.now(UTC) - timedelta(minutes=1),
                command_envelope=scheduled.model_dump(mode="json"),
                cancellation_event_types=["RunCompleted"],
                owner_user_id="orchestrator-user",
                team_id=None,
            )
        )
        await session.commit()

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-timer-race-claim"),
        )
        claims = await PostgresTimerStore(session).claim_due(
            limit=1,
            claim_ttl=timedelta(seconds=30),
        )
        assert len(claims) == 1
        assert claims[0].timer_id == timer_id
        await session.commit()

    result = await orchestrator.handle(complete)

    assert result.status == "accepted"
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-timer-cancel-check"),
        )
        timer = await session.get(ExecutionScheduledCommandORM, timer_id)
        event = await session.scalar(
            select(ExecutionEventORM).where(
                ExecutionEventORM.position == result.last_event_position
            )
        )
        assert timer is not None
        assert event is not None
        assert timer.status == "cancelled"
        assert timer.cancellation_event_id == event.event_id
        assert timer.claim_deadline is None
        assert (
            await session.get(
                ExecutionCommandInboxORM,
                deterministic_command_id,
            )
            is None
        )

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("orchestrator-timer-race-fire"),
        )
        assert not await PostgresTimerStore(session).mark_fired(
            claims[0],
            now=NOW,
        )
        await session.rollback()
