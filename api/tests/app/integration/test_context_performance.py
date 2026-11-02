"""Paired local recovery experiment, not a pre-P2 revision or AC21 capacity claim.

Five alternating pairs per size, serial on one DB/engine. The first pair uses
new sessions; subsequent pairs are warm. Structural gates are fixed before the
run: snapshot reads 22 events, state/next decision match the full replay, and at
1000 activities its median time must not exceed full replay. Raw samples remain
in JUnit; five samples are descriptive, not production percentile estimates.
"""

import json
import statistics
import tracemalloc
from time import perf_counter_ns, process_time_ns
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.domain.execution.aggregate import ReplaySnapshot, replay
from app.domain.execution.events import NewEvent
from app.domain.execution.run import RunAggregate
from app.domain.execution.store import AppendContext
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.execution.postgres_snapshot_store import PostgresSnapshotStore
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader
from app.infrastructure.security.db_authorization import configure_session_authorization
from tests.app.infrastructure.execution.test_snapshot_store import (
    EVENTS,
    NOW,
    seed_stream,
    snapshot_database,
)

loader_database = snapshot_database
pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("activities", [10, 100, 1000])
async def test_snapshot_tail_against_full_replay_same_formal_history(
    loader_database, activities, monkeypatch, record_property
):
    sessions, streams = loader_database
    stream, initial = await seed_stream(sessions, initial_events=EVENTS[:2])
    streams.append(stream)
    generated = []
    for ordinal in range(activities):
        identity = str(uuid4())
        generated.extend(
            [
                NewEvent(
                    event_type="ActivityRequested",
                    event_schema_version=1,
                    public_payload={
                        "activity_id": identity,
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
                        "activity_id": identity,
                        "generation": 0,
                        "result_ref": f"execution/results/{identity}/{ordinal}.json",
                        "result_summary": f"result {ordinal}",
                    },
                    internal_payload={},
                ),
            ]
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
    aggregate = RunAggregate()
    auth = AuthorizationContext.system("snapshot-performance-test")
    async with sessions() as db:
        await configure_session_authorization(db, auth)
        store = PostgresEventStore(db, event_registries={"run": aggregate.event_registry})
        await store.append(
            stream,
            len(initial),
            tuple(generated),
            AppendContext(
                owner_user_id="snapshot-user",
                team_id=None,
                correlation_id=uuid4(),
                causation_id=uuid4(),
                occurred_at=NOW,
            ),
        )
        facts = await store.load_stream("run", stream.stream_id)
        expected = replay(aggregate, facts, stream_id=stream.stream_id)
        prefix = replay(aggregate, facts[:-20], stream_id=stream.stream_id)
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
            serializer_version=aggregate.snapshot_serializer_version,
        )
        await db.commit()
    counts = {"sql": 0, "event_rows": 0, "event_decoded_json_bytes": 0}
    original = PostgresEventStore._to_stored

    def counted(row):
        value = original(row)
        counts["event_rows"] += 1
        counts["event_decoded_json_bytes"] += len(value.model_dump_json().encode())
        return value

    def sql_count(*_):
        counts["sql"] += 1

    monkeypatch.setattr(PostgresEventStore, "_to_stored", staticmethod(counted))
    engine = sessions.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", sql_count)
    samples = []
    try:
        for pair in range(5):
            for mode in (
                ["full_replay", "snapshot_tail"]
                if pair % 2 == 0
                else ["snapshot_tail", "full_replay"]
            ):
                async with sessions() as db:
                    await configure_session_authorization(db, auth)
                    counts.update(dict.fromkeys(counts, 0))
                    tracemalloc.start()
                    begin, cpu = perf_counter_ns(), process_time_ns()
                    try:
                        if mode == "full_replay":
                            rows = await PostgresEventStore(
                                db, event_registries={"run": aggregate.event_registry}
                            ).load_stream("run", stream.stream_id)
                            result = replay(aggregate, rows, stream_id=stream.stream_id)
                            snapshot_bytes = 0
                        else:
                            loaded = await PostgresVerifiedRunLoader(db).load(
                                stream.stream_id, owner_scope=("snapshot-user", None)
                            )
                            result, snapshot_bytes = loaded.replay, loaded.snapshot_bytes
                            assert loaded.tail_count == 20
                        elapsed, cpu_elapsed = perf_counter_ns() - begin, process_time_ns() - cpu
                        peak = tracemalloc.get_traced_memory()[1]
                    finally:
                        tracemalloc.stop()
                    samples.append(
                        {
                            "pair": pair,
                            "mode": mode,
                            "elapsed_ns": elapsed,
                            "cpu_ns": cpu_elapsed,
                            "peak_traced_bytes": peak,
                            "snapshot_bytes": snapshot_bytes,
                            **counts,
                        }
                    )
                    assert result == expected
                    assert counts["event_rows"] == (
                        22 if mode == "snapshot_tail" else 2 * activities + 22
                    )
    finally:
        event.remove(engine, "before_cursor_execute", sql_count)
    summary = {}
    for mode in ["full_replay", "snapshot_tail"]:
        times = sorted(row["elapsed_ns"] for row in samples if row["mode"] == mode)
        summary[mode] = {
            "p50_ns": statistics.median(times),
            "p95_nearest_rank_ns": times[-1],
            "samples": len(times),
        }
    record_property(
        "paired_recovery",
        json.dumps(
            {
                "activities": activities,
                "tail": 20,
                "concurrency": 1,
                "cache": "first-session-and-warm-pairs; database page cache uncontrolled",
                "reference": "full replay in same revision",
                "instrumentation": "SQL listener, event JSON byte counting and tracemalloc enabled in both paths",
                "samples": samples,
                "summary": summary,
                "errors": 0,
            }
        ),
    )
    if activities == 1000:
        assert summary["snapshot_tail"]["p50_ns"] <= summary["full_replay"]["p50_ns"]
