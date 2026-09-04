from datetime import UTC, datetime
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.execution.aggregate import ReplaySnapshot, replay
from app.domain.execution.events import NewEvent
from app.domain.execution.run import RunAggregate, RunState
from app.domain.execution.store import AppendContext, StreamRef
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import (
    ExecutionEventORM,
    ExecutionSnapshotORM,
)
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.execution.postgres_snapshot_store import PostgresSnapshotStore
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_session,
    execution_kernel_database_uri,
    run_policy_snapshot_json,
)

NOW = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)
EVENTS = (
    NewEvent(
        event_type="RunCreated",
        event_schema_version=1,
        public_payload={
            "family": "ask",
            "source_entity_type": "session",
            "source_entity_id": "snapshot-session",
            "parent_run_id": None,
            "input": {},
        },
        internal_payload={
            "semantic_payload": {},
            "policy_snapshot": run_policy_snapshot_json("ask"),
        },
    ),
    NewEvent(
        event_type="RunStarted",
        event_schema_version=1,
        public_payload={},
        internal_payload={},
    ),
    NewEvent(
        event_type="RunCompleted",
        event_schema_version=1,
        public_payload={"result_ref": "execution://snapshot-result"},
        internal_payload={},
    ),
)


def metric_sample(name: str, labels: dict | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


async def seed_stream(session_factory) -> tuple[StreamRef, tuple]:
    stream = StreamRef(
        stream_type="run",
        stream_id=str(uuid4()),
    )
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("snapshot-test-seed"),
        )
        result = await PostgresEventStore(session).append(
            stream,
            0,
            EVENTS,
            AppendContext(
                owner_user_id="snapshot-user",
                team_id=None,
                correlation_id=uuid4(),
                causation_id=uuid4(),
                occurred_at=NOW,
            ),
        )
        await session.commit()
    return stream, result.events


async def cleanup_stream(session_factory, stream: StreamRef) -> None:
    del session_factory
    async with execution_admin_session() as session:
        await session.execute(
            delete(ExecutionSnapshotORM).where(
                ExecutionSnapshotORM.stream_type == stream.stream_type,
                ExecutionSnapshotORM.stream_id == stream.stream_id,
            )
        )
        await session.execute(
            text("ALTER TABLE execution_events DISABLE TRIGGER execution_events_immutable")
        )
        await session.execute(
            delete(ExecutionEventORM).where(
                ExecutionEventORM.stream_type == stream.stream_type,
                ExecutionEventORM.stream_id == stream.stream_id,
            )
        )
        await session.execute(
            text("ALTER TABLE execution_events ENABLE TRIGGER execution_events_immutable")
        )
        await session.commit()


@pytest.fixture
async def snapshot_database(_db_schema):
    engine = create_async_engine(execution_kernel_database_uri())
    session_factory = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    streams: list[StreamRef] = []
    try:
        yield session_factory, streams
    finally:
        for stream in streams:
            await cleanup_stream(session_factory, stream)
        await engine.dispose()


@pytest.mark.asyncio
async def test_snapshot_tail_replay_equals_full_replay(snapshot_database) -> None:
    session_factory, streams = snapshot_database
    stream, events = await seed_stream(session_factory)
    streams.append(stream)
    aggregate = RunAggregate()
    prefix = replay(aggregate, events[:2], stream_id=stream.stream_id)
    candidate = ReplaySnapshot(
        stream_id=stream.stream_id,
        stream_version=prefix.stream_version,
        state=prefix.state,
        state_hash=prefix.state_hash,
        last_event_hash=prefix.last_event_hash,
    )

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("snapshot-test"),
        )
        store = PostgresSnapshotStore(session)
        await store.save(
            stream.stream_type,
            candidate,
            owner_user_id="snapshot-user",
            team_id=None,
            serializer_version=RunAggregate.snapshot_serializer_version,
        )
        await session.commit()

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("snapshot-test-load"),
        )
        loaded = await PostgresSnapshotStore(session).load(
            stream.stream_type,
            stream.stream_id,
            state_type=RunState,
            serializer_version=RunAggregate.snapshot_serializer_version,
        )

    assert loaded is not None
    resumed = replay(aggregate, events[2:], snapshot=loaded)
    full = replay(aggregate, events, stream_id=stream.stream_id)
    assert resumed.state == full.state
    assert resumed.state_hash == full.state_hash


@pytest.mark.asyncio
async def test_stale_serializer_snapshot_is_ignored(snapshot_database) -> None:
    session_factory, streams = snapshot_database
    stream, events = await seed_stream(session_factory)
    streams.append(stream)
    prefix = replay(RunAggregate(), events[:1], stream_id=stream.stream_id)
    candidate = ReplaySnapshot(
        stream_id=stream.stream_id,
        stream_version=prefix.stream_version,
        state=prefix.state,
        state_hash=prefix.state_hash,
        last_event_hash=prefix.last_event_hash,
    )

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("snapshot-stale-save"),
        )
        store = PostgresSnapshotStore(session)
        await store.save(
            stream.stream_type,
            candidate,
            owner_user_id="snapshot-user",
            team_id=None,
            serializer_version=1,
        )
        await session.commit()

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("snapshot-stale-load"),
        )
        loaded = await PostgresSnapshotStore(session).load(
            stream.stream_type,
            stream.stream_id,
            state_type=RunState,
            serializer_version=2,
        )

    assert loaded is None


@pytest.mark.asyncio
async def test_corrupt_snapshot_is_deleted_and_full_replay_remains_available(
    snapshot_database,
) -> None:
    session_factory, streams = snapshot_database
    stream, events = await seed_stream(session_factory)
    streams.append(stream)
    prefix = replay(RunAggregate(), events[:2], stream_id=stream.stream_id)
    candidate = ReplaySnapshot(
        stream_id=stream.stream_id,
        stream_version=prefix.stream_version,
        state=prefix.state,
        state_hash=prefix.state_hash,
        last_event_hash=prefix.last_event_hash,
    )

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("snapshot-corrupt-save"),
        )
        store = PostgresSnapshotStore(session)
        await store.save(
            stream.stream_type,
            candidate,
            owner_user_id="snapshot-user",
            team_id=None,
            serializer_version=RunAggregate.snapshot_serializer_version,
        )
        await session.commit()
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("snapshot-corrupt-inject"),
        )
        await session.execute(
            update(ExecutionSnapshotORM)
            .where(
                ExecutionSnapshotORM.stream_type == stream.stream_type,
                ExecutionSnapshotORM.stream_id == stream.stream_id,
            )
            .values(state={"stream_id": stream.stream_id, "status": "failed"})
        )
        await session.commit()

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("snapshot-corrupt-load"),
        )
        # An unparseable state counts as schema drift, not corruption: the
        # corruption alarm is reserved for parseable-state / wrong-hash rows.
        labels = {"reason": "snapshot_schema_drift"}
        before_failures = metric_sample(
            "execution_replay_failures_total",
            labels,
        )
        loaded = await PostgresSnapshotStore(session).load(
            stream.stream_type,
            stream.stream_id,
            state_type=RunState,
            serializer_version=RunAggregate.snapshot_serializer_version,
        )
        assert metric_sample("execution_replay_failures_total", labels) - before_failures == 1
        await session.commit()
    assert loaded is None
    assert replay(RunAggregate(), events, stream_id=stream.stream_id).state.status == ("completed")

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("snapshot-corrupt-verify"),
        )
        assert (
            await session.scalar(
                select(ExecutionSnapshotORM).where(
                    ExecutionSnapshotORM.stream_type == stream.stream_type,
                    ExecutionSnapshotORM.stream_id == stream.stream_id,
                )
            )
            is None
        )


@pytest.mark.asyncio
async def test_parseable_snapshot_with_wrong_hash_counts_as_corruption(
    snapshot_database,
) -> None:
    session_factory, streams = snapshot_database
    stream, events = await seed_stream(session_factory)
    streams.append(stream)
    prefix = replay(RunAggregate(), events[:2], stream_id=stream.stream_id)
    candidate = ReplaySnapshot(
        stream_id=stream.stream_id,
        stream_version=prefix.stream_version,
        state=prefix.state,
        state_hash=prefix.state_hash,
        last_event_hash=prefix.last_event_hash,
    )
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("snapshot-hash-save"),
        )
        await PostgresSnapshotStore(session).save(
            stream.stream_type,
            candidate,
            owner_user_id="snapshot-user",
            team_id=None,
            serializer_version=RunAggregate.snapshot_serializer_version,
        )
        await session.commit()
    tampered = prefix.state.model_copy(update={"retry_generation": 9}).model_dump(mode="json")
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("snapshot-hash-inject"),
        )
        await session.execute(
            update(ExecutionSnapshotORM)
            .where(
                ExecutionSnapshotORM.stream_type == stream.stream_type,
                ExecutionSnapshotORM.stream_id == stream.stream_id,
            )
            .values(state=tampered)
        )
        await session.commit()

    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("snapshot-hash-load"),
        )
        labels = {"reason": "snapshot_hash_mismatch"}
        before_failures = metric_sample("execution_replay_failures_total", labels)
        loaded = await PostgresSnapshotStore(session).load(
            stream.stream_type,
            stream.stream_id,
            state_type=RunState,
            serializer_version=RunAggregate.snapshot_serializer_version,
        )
        assert metric_sample("execution_replay_failures_total", labels) - before_failures == 1
        await session.commit()
    assert loaded is None


@pytest.mark.asyncio
async def test_snapshot_delete_removes_only_requested_stream(snapshot_database) -> None:
    session_factory, streams = snapshot_database
    stream, events = await seed_stream(session_factory)
    streams.append(stream)
    prefix = replay(RunAggregate(), events[:1], stream_id=stream.stream_id)
    candidate = ReplaySnapshot(
        stream_id=stream.stream_id,
        stream_version=prefix.stream_version,
        state=prefix.state,
        state_hash=prefix.state_hash,
        last_event_hash=prefix.last_event_hash,
    )
    async with session_factory() as session:
        await configure_session_authorization(
            session,
            AuthorizationContext.system("snapshot-delete"),
        )
        store = PostgresSnapshotStore(session)
        await store.save(
            stream.stream_type,
            candidate,
            owner_user_id="snapshot-user",
            team_id=None,
            serializer_version=RunAggregate.snapshot_serializer_version,
        )
        assert await store.delete(stream.stream_type, stream.stream_id) == 1
        await session.commit()
