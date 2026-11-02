"""Real PostgreSQL snapshot recovery, including fail-closed corruption."""

import asyncio
import json
from time import perf_counter_ns
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update

from app.domain.execution.aggregate import ReplaySnapshot, replay
from app.domain.execution.events import NewEvent
from app.domain.execution.run import RunAggregate
from app.domain.execution.store import (
    AppendContext,
    CorruptEventStreamError,
    StreamOwnerScopeMismatchError,
)
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import ExecutionEventORM, ExecutionSnapshotORM
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.execution.postgres_snapshot_store import PostgresSnapshotStore
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader
from app.infrastructure.security.db_authorization import configure_session_authorization
from tests.app.execution_test_support import execution_admin_session
from tests.app.infrastructure.execution.test_snapshot_store import (
    seed_stream,
    snapshot_database,
)

loader_database = snapshot_database

pytestmark = pytest.mark.asyncio


async def prepared(loader_database):
    sessions, streams = loader_database
    stream, events = await seed_stream(sessions)
    streams.append(stream)
    async with sessions() as db:
        await configure_session_authorization(
            db, AuthorizationContext.system("verified-loader-test")
        )
        for count in (1, 2):
            value = replay(RunAggregate(), events[:count], stream_id=stream.stream_id)
            await PostgresSnapshotStore(db).save(
                "run",
                ReplaySnapshot(
                    stream_id=stream.stream_id,
                    stream_version=count,
                    state=value.state,
                    state_hash=value.state_hash,
                    last_event_hash=value.last_event_hash,
                ),
                owner_user_id="snapshot-user",
                team_id=None,
                serializer_version=RunAggregate.snapshot_serializer_version,
            )
        await db.commit()
    return sessions, stream, events


async def test_s01_snapshot_tail_and_at_version_are_equivalent(loader_database):
    sessions, stream, events = await prepared(loader_database)
    async with sessions() as db:
        await configure_session_authorization(
            db, AuthorizationContext.system("verified-loader-test")
        )
        loader = PostgresVerifiedRunLoader(db)
        full = await loader.load(stream.stream_id, owner_scope=("snapshot-user", None))
        cut = await loader.load(stream.stream_id, owner_scope=("snapshot-user", None), at_version=2)
    assert full.replay == replay(RunAggregate(), events, stream_id=stream.stream_id)
    assert full.snapshot_version == 2
    assert full.tail_count == 1
    assert full.snapshot_bytes > 0
    assert cut.replay == replay(RunAggregate(), events[:2], stream_id=stream.stream_id)
    assert cut.snapshot_version == 2
    assert cut.tail_count == 0


async def test_s02_only_bad_selected_anchor_snapshot_is_deleted(loader_database):
    sessions, stream, events = await prepared(loader_database)
    async with sessions() as db:
        await configure_session_authorization(
            db, AuthorizationContext.system("verified-loader-test")
        )
        await db.execute(
            update(ExecutionSnapshotORM)
            .where(
                ExecutionSnapshotORM.stream_id == stream.stream_id,
                ExecutionSnapshotORM.stream_version == 2,
            )
            .values(last_event_hash="f" * 64)
        )
        result = await PostgresVerifiedRunLoader(db).load(
            stream.stream_id, owner_scope=("snapshot-user", None)
        )
        versions = (
            await db.scalars(
                select(ExecutionSnapshotORM.stream_version).where(
                    ExecutionSnapshotORM.stream_id == stream.stream_id
                )
            )
        ).all()
        await db.commit()
    assert versions == [1]
    assert result.snapshot_version is None
    assert result.tail_count == 3
    assert result.replay == replay(RunAggregate(), events, stream_id=stream.stream_id)


@pytest.mark.parametrize("version", [2, 3])
async def test_s03_corrupt_anchor_or_tail_never_falls_back(loader_database, version):
    sessions, stream, _events = await prepared(loader_database)
    async with execution_admin_session() as admin:
        await admin.execute(
            text("ALTER TABLE execution_events DISABLE TRIGGER execution_events_immutable")
        )
        await admin.execute(
            update(ExecutionEventORM)
            .where(
                ExecutionEventORM.stream_id == stream.stream_id,
                ExecutionEventORM.stream_version == version,
            )
            .values(event_hash="e" * 64)
        )
        await admin.execute(
            text("ALTER TABLE execution_events ENABLE TRIGGER execution_events_immutable")
        )
        await admin.commit()
    async with sessions() as db:
        await configure_session_authorization(
            db, AuthorizationContext.system("verified-loader-test")
        )
        with pytest.raises(CorruptEventStreamError):
            await PostgresVerifiedRunLoader(db).load(
                stream.stream_id, owner_scope=("snapshot-user", None)
            )
        assert (
            len(
                (
                    await db.scalars(
                        select(ExecutionSnapshotORM).where(
                            ExecutionSnapshotORM.stream_id == stream.stream_id
                        )
                    )
                ).all()
            )
            == 2
        )
        await db.rollback()


async def test_s04_foreign_scope_is_rejected_before_snapshot_read(loader_database):
    sessions, stream, _events = await prepared(loader_database)
    async with sessions() as db:
        await configure_session_authorization(
            db, AuthorizationContext.system("verified-loader-test")
        )
        with pytest.raises(StreamOwnerScopeMismatchError):
            await PostgresVerifiedRunLoader(db).load(
                stream.stream_id, owner_scope=("foreign-user", None)
            )
        with pytest.raises(ValueError, match="version is unavailable"):
            await PostgresVerifiedRunLoader(db).load(
                stream.stream_id, owner_scope=("snapshot-user", None), at_version=4
            )


async def test_s05_concurrent_snapshot_publication_keeps_the_appended_tail(
    loader_database, monkeypatch
):
    from tests.app.infrastructure.execution.test_snapshot_store import NOW

    sessions, stream, events = await prepared(loader_database)
    selected, published = asyncio.Event(), asyncio.Event()
    load_stream = PostgresEventStore.load_stream

    async def pause_after_snapshot(store, stream_type, stream_id, **kwargs):
        if stream_id == stream.stream_id and kwargs.get("after_version") == 2:
            selected.set()
            await asyncio.wait_for(published.wait(), timeout=10)
        return await load_stream(store, stream_type, stream_id, **kwargs)

    monkeypatch.setattr(PostgresEventStore, "load_stream", pause_after_snapshot)
    async with sessions() as reader:
        await configure_session_authorization(
            reader, AuthorizationContext.system("verified-loader-test")
        )
        pending = asyncio.create_task(
            PostgresVerifiedRunLoader(reader).load(
                stream.stream_id, owner_scope=("snapshot-user", None)
            )
        )
        try:
            await asyncio.wait_for(selected.wait(), timeout=10)
            async with sessions() as writer:
                await configure_session_authorization(
                    writer, AuthorizationContext.system("verified-loader-test")
                )
                appended = await PostgresEventStore(writer).append(
                    stream,
                    3,
                    (
                        NewEvent(
                            event_type="ModelUsageRecorded",
                            event_schema_version=1,
                            public_payload={},
                            internal_payload={"call_identity": str(uuid4()), "phase": "settlement"},
                        ),
                    ),
                    AppendContext(
                        owner_user_id="snapshot-user",
                        team_id=None,
                        correlation_id=uuid4(),
                        causation_id=uuid4(),
                        occurred_at=NOW,
                    ),
                )
                expected = replay(
                    RunAggregate(), (*events, *appended.events), stream_id=stream.stream_id
                )
                await PostgresSnapshotStore(writer).save(
                    "run",
                    ReplaySnapshot(
                        stream_id=stream.stream_id,
                        stream_version=expected.stream_version,
                        state=expected.state,
                        state_hash=expected.state_hash,
                        last_event_hash=expected.last_event_hash,
                    ),
                    owner_user_id="snapshot-user",
                    team_id=None,
                    serializer_version=RunAggregate.snapshot_serializer_version,
                )
                await writer.commit()
            published.set()
            loaded = await asyncio.wait_for(pending, timeout=10)
            assert loaded.snapshot_version == 2
            assert loaded.tail_count == 2
            assert loaded.replay == expected
            latest = await PostgresVerifiedRunLoader(reader).load(
                stream.stream_id, owner_scope=("snapshot-user", None)
            )
            assert latest.snapshot_version == 4
            assert latest.tail_count == 0
            assert latest.replay == expected
        finally:
            published.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("prefix_count", [50, 500, 2000])
async def test_fixed_twenty_tail_reads_do_not_expand_with_event_prefix(
    loader_database, prefix_count, monkeypatch, record_property
):
    from tests.app.infrastructure.execution.test_snapshot_store import NOW

    sessions, stream, events = await prepared(loader_database)
    additional = tuple(
        NewEvent(
            event_type="ModelUsageRecorded",
            event_schema_version=1,
            public_payload={},
            internal_payload={"call_identity": str(uuid4()), "phase": "settlement"},
        )
        for _ in range(prefix_count + 20)
    )
    async with sessions() as db:
        await configure_session_authorization(
            db, AuthorizationContext.system("verified-loader-test")
        )
        appended = await PostgresEventStore(db).append(
            stream,
            3,
            additional,
            AppendContext(
                owner_user_id="snapshot-user",
                team_id=None,
                correlation_id=uuid4(),
                causation_id=uuid4(),
                occurred_at=NOW,
            ),
        )
        all_events = (*events, *appended.events)
        prefix = replay(RunAggregate(), all_events[:-20], stream_id=stream.stream_id)
        await PostgresSnapshotStore(db).save(
            "run",
            ReplaySnapshot(
                stream_id=stream.stream_id,
                stream_version=prefix.stream_version,
                state=prefix.state,
                state_hash=prefix.state_hash,
                last_event_hash=prefix.last_event_hash,
            ),
            owner_user_id="snapshot-user",
            team_id=None,
            serializer_version=RunAggregate.snapshot_serializer_version,
        )
        await db.commit()
    converted = []
    original = PostgresEventStore._to_stored

    def counted(row):
        converted.append(row.stream_version)
        return original(row)

    monkeypatch.setattr(PostgresEventStore, "_to_stored", staticmethod(counted))
    samples = []
    async with sessions() as db:
        await configure_session_authorization(
            db, AuthorizationContext.system("verified-loader-test")
        )
        loader = PostgresVerifiedRunLoader(db)
        for _ in range(5):
            converted.clear()
            started = perf_counter_ns()
            value = await loader.load(stream.stream_id, owner_scope=("snapshot-user", None))
            samples.append(
                {
                    "elapsed_ns": perf_counter_ns() - started,
                    "event_rows": len(converted),
                    "snapshot_bytes": value.snapshot_bytes,
                }
            )
            assert value.tail_count == 20
            assert len(converted) == 22  # origin + anchor + twenty tail events
            assert value.replay == replay(RunAggregate(), all_events, stream_id=stream.stream_id)
    record_property(
        "raw_hot_loader_samples", json.dumps({"prefix_count": prefix_count, "samples": samples})
    )


@pytest.mark.parametrize("activity_count", [10, 100, 1000])
async def test_growing_snapshot_state_has_bounded_event_reads_and_measured_decode_cost(
    loader_database, activity_count, monkeypatch, record_property
):
    import tracemalloc
    from time import process_time_ns

    from tests.app.infrastructure.execution.test_snapshot_store import EVENTS, NOW

    sessions, streams = loader_database
    stream, initial = await seed_stream(sessions, initial_events=EVENTS[:2])
    streams.append(stream)
    generated = []
    for ordinal in range(activity_count):
        activity = str(uuid4())
        generated.extend(
            (
                NewEvent(
                    event_type="ActivityRequested",
                    event_schema_version=1,
                    public_payload={
                        "activity_id": activity,
                        "activity_type": "model.call",
                        "timeout_at": NOW.isoformat(),
                        "input_digest": "a" * 64,
                    },
                    internal_payload={"input_payload": {}},
                ),
                NewEvent(
                    event_type="ActivityCompleted",
                    event_schema_version=1,
                    public_payload={
                        "activity_id": activity,
                        "generation": 0,
                        "result_ref": f"execution/results/{activity}/{ordinal}.json",
                        "result_summary": f"bounded result {ordinal}",
                    },
                    internal_payload={},
                ),
            )
        )
    generated.extend(
        NewEvent(
            event_type="ModelUsageRecorded",
            event_schema_version=1,
            public_payload={},
            internal_payload={"call_identity": str(uuid4()), "phase": "settlement"},
        )
        for _ in range(20)
    )
    async with sessions() as writer:
        await configure_session_authorization(
            writer, AuthorizationContext.system("verified-loader-test")
        )
        appended = await PostgresEventStore(writer).append(
            stream,
            2,
            tuple(generated),
            AppendContext(
                owner_user_id="snapshot-user",
                team_id=None,
                correlation_id=uuid4(),
                causation_id=uuid4(),
                occurred_at=NOW,
            ),
        )
        # Decode stored v1 facts through the same registry used by the loader.
        all_events = await PostgresEventStore(
            writer, event_registries={"run": RunAggregate().event_registry}
        ).load_stream("run", stream.stream_id)
        assert len(all_events) == len(initial) + len(appended.events)
        prefix = replay(RunAggregate(), all_events[:-20], stream_id=stream.stream_id)
        await PostgresSnapshotStore(writer).save(
            "run",
            ReplaySnapshot(
                stream_id=stream.stream_id,
                stream_version=prefix.stream_version,
                state=prefix.state,
                state_hash=prefix.state_hash,
                last_event_hash=prefix.last_event_hash,
            ),
            owner_user_id="snapshot-user",
            team_id=None,
            serializer_version=RunAggregate.snapshot_serializer_version,
        )
        await writer.commit()
    converted, samples = [], []
    original = PostgresEventStore._to_stored

    def counted(row):
        converted.append(row.stream_version)
        return original(row)

    monkeypatch.setattr(PostgresEventStore, "_to_stored", staticmethod(counted))
    expected = replay(RunAggregate(), all_events, stream_id=stream.stream_id)
    async with sessions() as reader:
        await configure_session_authorization(
            reader, AuthorizationContext.system("verified-loader-test")
        )
        for _ in range(5):
            converted.clear()
            tracemalloc.start()
            started, cpu = perf_counter_ns(), process_time_ns()
            try:
                loaded = await PostgresVerifiedRunLoader(reader).load(
                    stream.stream_id, owner_scope=("snapshot-user", None)
                )
                samples.append(
                    {
                        "elapsed_ns": perf_counter_ns() - started,
                        "cpu_ns": process_time_ns() - cpu,
                        "peak_traced_bytes": tracemalloc.get_traced_memory()[1],
                        "snapshot_bytes": loaded.snapshot_bytes,
                        "event_rows": len(converted),
                    }
                )
            finally:
                tracemalloc.stop()
            assert loaded.tail_count == 20
            assert len(converted) == 22
            assert len(loaded.replay.state.activity_results) == activity_count
            assert loaded.snapshot_bytes > activity_count * 200
            assert loaded.replay == expected
    record_property(
        "raw_growing_state_samples",
        json.dumps({"completed_activities": activity_count, "samples": samples}),
    )
