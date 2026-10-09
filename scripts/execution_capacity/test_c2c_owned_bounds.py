"""Private temporary files only; no live observer clients or host-clock reads."""

import json

import pytest
from scripts.execution_capacity.attempt import AttemptLedger
from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError
from scripts.execution_capacity.guest_seal import read_private


def test_child_budgets_cannot_multiply_unit_quota():
    root = EvidenceBudget(bytes_limit=100, rows_limit=10, row_limit=100)
    first, second = root.child(), root.child()
    first.charge_bytes(60)
    with pytest.raises(EvidenceQuotaError, match="quota"):
        second.charge_bytes(41)
    assert (root.bytes, first.bytes, second.bytes) == (60, 60, 0)
    second.charge_bytes(40)
    assert root.bytes == 100


def test_readonly_ledger_reopens_original_bytes_without_lock_or_clock(tmp_path):
    from scripts.execution_capacity.attempt import ReadOnlyAttemptLedger

    root = tmp_path / "origin"
    with AttemptLedger.create(root, {"attempt_id": "fixture"}) as ledger:
        ledger.append("example", {"private": "body"})
    (root / "attempt.lock").unlink()
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    view = ReadOnlyAttemptLedger.open(root, origin=root, budget=EvidenceBudget())
    assert view.plan == {"attempt_id": "fixture"}
    assert view.records("example")[0]["body"] == {"private": "body"}
    assert view.root == root
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    assert not hasattr(view, "append")
    assert not hasattr(view, "bind_clock")


def test_readonly_ledger_rejects_chain_change_and_oversized_frame(tmp_path):
    from scripts.execution_capacity.attempt import ReadOnlyAttemptLedger

    root = tmp_path / "origin"
    with AttemptLedger.create(root, {"attempt_id": "fixture"}) as ledger:
        ledger.append("example", {"private": "body"})
    path = root / "attempt.jsonl"
    original = path.read_bytes()
    changed = json.loads(original)
    changed["body"] = {"private": "changed"}
    path.write_text(json.dumps(changed) + "\n")
    with pytest.raises(ValueError, match="chain"):
        ReadOnlyAttemptLedger.open(root, origin=root, budget=EvidenceBudget())
    path.write_bytes(original)
    with pytest.raises(EvidenceQuotaError, match="frame"):
        ReadOnlyAttemptLedger.open(root, origin=root, budget=EvidenceBudget(row_limit=50))


def test_private_legacy_ceiling_checked_before_json_parser(tmp_path, monkeypatch):
    path = tmp_path / "private.json"
    path.touch(mode=0o600)
    with path.open("wb") as handle:
        handle.truncate(32 * 1024 * 1024 + 1)

    def forbidden(*args, **kwargs):
        pytest.fail("oversized private input reached JSON parser")

    monkeypatch.setattr(json, "load", forbidden)
    monkeypatch.setattr(json, "loads", forbidden)
    with pytest.raises(EvidenceQuotaError, match="private"):
        read_private(path)


def test_bounded_journal_point_read_checks_null_shape_before_payload(tmp_path):
    from scripts.execution_capacity.observers import RecoveryJournal

    root = tmp_path / "journal"
    root.mkdir(mode=0o700)
    with RecoveryJournal(root) as journal:
        journal.intent("lease", "a", {"payload": "x" * 500})
        statements = []
        journal.db.set_trace_callback(statements.append)
        with pytest.raises(EvidenceQuotaError, match="quota"):
            journal.bounded_get("lease", "a", EvidenceBudget(row_limit=100))
        assert not any(s.startswith("SELECT identity,body,receipt") for s in statements)
        assert journal.bounded_get("lease", "a", EvidenceBudget())["body"]["payload"] == "x" * 500
        assert journal.bounded_get("lease", "absent", EvidenceBudget()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ok", "truncated", "overreturn", "close"])
async def test_evidence_objects_retain_original_or_fail_with_closed_transport(mode):
    from types import SimpleNamespace

    from scripts.execution_capacity.evidence_objects import EvidenceObjects

    from app.infrastructure.adapters.object_storage import MinioObjectStorageAdapter

    class Response:
        closed = False
        released = False
        offset = 0

        def read(self, count):
            data = b"abcdef" if mode != "ok" else b"abc"
            if mode == "overreturn":
                return b"x" * (count + 1)
            chunk = data[self.offset : self.offset + count]
            self.offset += len(chunk)
            return chunk

        def close(self):
            self.closed = True
            if mode == "close":
                raise OSError("fixture close failure")

        def release_conn(self):
            self.released = True

    response = Response()
    raw = SimpleNamespace(
        bucket="fixture", client=SimpleNamespace(get_object=lambda *args: response)
    )
    objects = EvidenceObjects(MinioObjectStorageAdapter(raw), EvidenceBudget(), object_limit=4)
    if mode == "ok":
        assert await objects.get_bytes("private-key") == b"abc"
        assert objects.originals[0]["data"] == b"abc"
        assert objects.originals[0]["error"] is None
    else:
        with pytest.raises((EvidenceQuotaError, OSError)):
            await objects.get_bytes("private-key")
        assert objects.originals[0]["error"] is not None
    assert response.closed
    assert response.released


@pytest.mark.asyncio
async def test_playback_rejects_huge_gap_range_before_any_read_or_state_allocation():
    from datetime import UTC, datetime
    from uuid import UUID

    from scripts.execution_capacity.evidence_owner import EvidenceOwner

    from app.domain.models.playback import PlaybackBoundary
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.execution.postgres_playback import load_playback

    class NoRead:
        async def execute(self, *args, **kwargs):
            pytest.fail("unbounded playback reached a source read")

    boundary = PlaybackBoundary(
        run_id=UUID(int=1),
        formal_position=1,
        progress_position=0,
        observed_order=10**12,
        projection_revision=10**12,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        projector_version=1,
    )
    with pytest.raises(EvidenceQuotaError, match="quota"):
        await load_playback(
            NoRead(),
            boundary,
            trusted_scope=OwnerScope.personal("fixture"),
            evidence=EvidenceOwner(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("oversize", [False, True])
async def test_playback_retains_all_original_nested_operands_and_empty_families(
    monkeypatch, oversize
):
    from datetime import UTC, datetime
    from uuid import UUID

    from scripts.execution_capacity.evidence_owner import EvidenceOwner

    from app.domain.models.playback import PlaybackBoundary
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.execution.postgres_playback import load_playback
    from app.infrastructure.models.execution_view import (
        ExecutionRunViewORM,
        ExecutionViewObservationORM,
    )

    boundary = PlaybackBoundary(
        run_id=UUID(int=1),
        formal_position=1,
        progress_position=0,
        observed_order=1,
        projection_revision=1,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        projector_version=1,
    )
    run = ExecutionRunViewORM(run_id=boundary.run_id, projector_version=1, completeness={})
    observation = ExecutionViewObservationORM(
        run_id=boundary.run_id,
        formal_position=1,
        progress_position=0,
        observed_order=1,
        public_payload={
            "facts": [{"kind": "run", "id": str(boundary.run_id), "patch": {"status": "completed"}}]
        },
    )

    from scripts.execution_capacity.observer_session import ObserverSession
    from sqlalchemy import Integer, create_engine, literal, select
    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import AsyncSession

    engine = create_engine("sqlite://")
    owner = EvidenceOwner()
    from app.infrastructure.models.execution_view import ExecutionPlaybackCheckpointORM

    run.scope_key = observation.scope_key = "user:fixture"
    observation.projector_version = observation.projection_revision = 1
    observation.observed_at = boundary.observed_at
    from sqlalchemy.types import DateTime, TypeDecorator

    class UTCDateTime(TypeDecorator):
        impl = DateTime
        cache_ok = True

        def process_result_value(self, value, dialect):
            return value.replace(tzinfo=UTC) if value is not None else None

    monkeypatch.setattr(ExecutionViewObservationORM.__table__.c.observed_at, "type", UTCDateTime())
    with engine.begin() as connection:
        for model in (
            ExecutionRunViewORM,
            ExecutionViewObservationORM,
            ExecutionPlaybackCheckpointORM,
        ):
            columns = ",".join(
                '"' + c.name + '" ' + ("INTEGER" if isinstance(c.type, Integer) else "TEXT")
                for c in model.__table__.columns
            )
            connection.exec_driver_sql(
                "CREATE TABLE " + model.__table__.name + " (" + columns + ")"
            )
        for model in (run, observation):
            connection.execute(
                type(model).__table__.insert(),
                {c.name: getattr(model, c.name) for c in type(model).__table__.columns},
            )
    original = Connection.execute
    calls = []

    def driver(connection, statement, parameters=None, *args, **kwargs):
        if statement.get_execution_options().get("c2c_preflight"):
            calls.append(("preflight", connection, parameters))
            values = {
                "row_count": 1,
                "max_bytes": 2**21 if oversize else 30,
                "total_bytes": 2**21 if oversize else 30,
                "read_only": "on",
                "isolation": "repeatable read",
                "snapshot": "playback:1",
            }
            return original(connection, select(*[literal(v).label(k) for k, v in values.items()]))
        calls.append(("typed", connection, parameters))
        return original(connection, statement, parameters, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", driver)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    async with AsyncSession(sync_session_class=Bound) as session:
        if oversize:
            with pytest.raises(EvidenceQuotaError):
                await load_playback(
                    session, boundary, trusted_scope=OwnerScope.personal("fixture"), evidence=owner
                )
            assert [c[0] for c in calls] == ["preflight"]
            return
        result = await load_playback(
            session, boundary, trusted_scope=OwnerScope.personal("fixture"), evidence=owner
        )
    assert [c[0] for c in calls] == ["preflight", "typed"] * 6
    assert all(calls[i][1:] == calls[i + 1][1:] for i in range(0, len(calls), 2))
    assert len(owner.sql_reads) == 6
    assert owner.sql_reads[0]["bound_parameters"]["run_id_1"] == boundary.run_id
    engine.dispose()
    assert result.state["run"][str(boundary.run_id)] == {"status": "completed"}
    assert owner.originals["playback-checkpoint"] == [None]
    assert owner.originals["playback-missing"] == [[]]
    assert owner.originals["playback-orders"] == [[1]]
    assert owner.originals["playback-observations"][0][0]["public_payload"]["facts"][0][
        "patch"
    ] == {"status": "completed"}


def test_cumulative_history_uses_one_checked_budget_and_exact_receipt_union(tmp_path):
    from scripts.execution_capacity.final_inventory import CumulativeJournal
    from scripts.execution_capacity.observers import RecoveryJournal

    for name in ("old", "new"):
        (tmp_path / name).mkdir(mode=0o700)
    with RecoveryJournal(tmp_path / "old") as old, RecoveryJournal(tmp_path / "new") as new:
        old.intent("lease", "one", {"private": "body"})
        new.intent("lease", "one", {"private": "body"})
        new.acknowledge("lease", "one", {"result": "receipt"})
        history = CumulativeJournal([old], new, budget=EvidenceBudget())
        assert history.records("lease") == [
            ("one", {"body": {"private": "body"}, "receipt": {"result": "receipt"}})
        ]
        tiny = CumulativeJournal([old], new, budget=EvidenceBudget(bytes_limit=30))
        with pytest.raises(EvidenceQuotaError, match="quota"):
            tiny.records("lease")


def test_source_file_quota_precedes_bytes_and_symlink_is_rejected(tmp_path):
    from scripts.execution_capacity.evidence_files import stream_file

    path = tmp_path / "source"
    path.write_bytes(b"original bytes")
    chunks = []
    with pytest.raises(EvidenceQuotaError, match="quota"):
        stream_file(path, budget=EvidenceBudget(bytes_limit=5), consumer=chunks.append)
    assert chunks == []
    assert stream_file(path, budget=EvidenceBudget(), consumer=chunks.append) == 14
    assert b"".join(chunks) == b"original bytes"
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        stream_file(link, budget=EvidenceBudget(), consumer=chunks.append)


def test_broker_preflights_original_result_before_decode_and_preserves_bytes(tmp_path, monkeypatch):
    import sqlite3

    from scripts.execution_capacity.broker_inventory import sqlite_pages

    path = tmp_path / "broker.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE operations(identity TEXT, fingerprint TEXT, result TEXT)")
        db.execute("CREATE TABLE bindings(identity TEXT, fingerprint TEXT)")
        db.execute(
            "INSERT INTO operations VALUES(?,?,?)", ("one", "a" * 64, '{ "exact" : "body" }')
        )
    pages = list(sqlite_pages(path))
    assert pages[1]["rows"][0]["result_raw"] == '{ "exact" : "body" }'
    statements = []
    original = sqlite3.connect

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(ValueError, match="quota"):
        list(sqlite_pages(path, row_limit=10))
    assert not any(s.startswith("SELECT identity,fingerprint,result") for s in statements)


def test_failure_prefix_uses_prepaid_same_unit_allowance(tmp_path):
    from types import SimpleNamespace

    from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.guest_seal import read_private

    owner = EvidenceOwner(budget=EvidenceBudget(bytes_limit=4 * 1024 * 1024, rows_limit=10000))
    prepaid = owner.budget.bytes
    assert prepaid == owner.failure_budget.bytes_limit
    owner.budget.reserve(owner.budget.bytes_limit - prepaid, rows=0)
    with pytest.raises(EvidenceQuotaError):
        owner.budget.reserve(1, rows=0)
    resources = SimpleNamespace(
        evidence_transport=SimpleNamespace(
            originals=[
                {
                    "stdout": b"raw-prefix" * 1000,
                    "stderr": b"",
                    "error": "EvidenceQuotaError",
                    "start_ns": 1,
                    "end_ns": 2,
                    "returncode": -9,
                }
            ]
        )
    )
    owner.retain_failure(tmp_path, EvidenceQuotaError("fixture"), resources=resources)
    value = read_private(tmp_path / "c2c-failed-originals.json")
    assert value["state"] == "failed"
    assert value["complete"] is False
    assert value["transport_prefixes"][0]["stdout"]["truncated"] is True
    assert owner.budget.bytes == owner.budget.bytes_limit
    assert not (tmp_path / "c2c-originals" / "manifest.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "fault"),
    [
        (route, fault)
        for route in ("playback", "restore")
        for fault in ("checkpoint", "mismatch", "absent", "read_error")
    ],
)
async def test_actual_boundary_observation_survives_checkpoint_or_failure(
    monkeypatch, route, fault
):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import UUID

    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from sqlalchemy.engine import IteratorResult
    from sqlalchemy.engine.result import SimpleResultMetaData

    from app.domain.models.playback import PlaybackBoundary
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.execution.postgres_execution_view import (
        PostgresExecutionView,
        ViewRevisionExpired,
    )
    from app.infrastructure.execution.postgres_playback import PlaybackUnavailable, load_playback
    from app.infrastructure.models.execution_view import (
        ExecutionPlaybackCheckpointORM,
        ExecutionRunViewORM,
    )

    boundary = PlaybackBoundary(
        run_id=UUID(int=1),
        formal_position=1,
        progress_position=0,
        observed_order=1,
        projection_revision=1,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        projector_version=1,
    )
    raw = {
        "formal_position": 2 if fault == "mismatch" else 1,
        "progress_position": 0,
        "projection_revision": 1,
        "observed_at": boundary.observed_at,
    }
    run = ExecutionRunViewORM(run_id=boundary.run_id, projector_version=1, completeness={})
    checkpoint = ExecutionPlaybackCheckpointORM(
        id=UUID(int=2),
        run_id=boundary.run_id,
        observed_order=1,
        formal_position=1,
        progress_position=0,
        state_ref={
            "schema_version": 1,
            "projector_version": 1,
            "state": {"run": {str(boundary.run_id): {"status": "completed"}}},
            "missing_intervals": [],
        },
    )

    class Session:
        scalars_values = iter([run, checkpoint])
        lists = iter([[[]], [1], []])

        async def execute(self, *args, **kwargs):
            if fault == "read_error":
                raise RuntimeError("fixture read failure")
            return IteratorResult(
                SimpleResultMetaData(list(raw)),
                iter([] if fault == "absent" else [tuple(raw.values())]),
            )

        async def scalar(self, *args, **kwargs):
            return next(self.scalars_values)

        async def scalars(self, *args, **kwargs):
            return SimpleNamespace(all=lambda: next(self.lists))

    owner = EvidenceOwner()
    scope = OwnerScope.personal("fixture")

    async def coverage(session, scope, boundary, *, evidence):
        assert evidence is owner
        return "fixture-token", None, None

    monkeypatch.setattr("app.infrastructure.execution.postgres_execution_view._coverage", coverage)
    if route == "playback":
        call = load_playback(Session(), boundary, trusted_scope=scope, evidence=owner)
    else:
        call = PostgresExecutionView(
            session_factory=None, authorization=None, evidence=owner
        ).restore(Session(), scope, boundary, "live")
    if fault == "checkpoint":
        result = await call
        assert result.state["run"][str(boundary.run_id)] == {"status": "completed"}
        assert owner.originals["playback-observations"] == [[]]
    else:
        with pytest.raises(
            RuntimeError if fault == "read_error" else (PlaybackUnavailable, ViewRevisionExpired)
        ):
            await call
    originals = owner.originals["playback-boundary-observation"]
    assert len(originals) == (2 if (route, fault) == ("restore", "checkpoint") else 1)
    retained = originals[0]
    assert retained["identity"] == {
        "run_id": boundary.run_id,
        "scope_key": "user:fixture",
        "projector_version": 1,
        "observed_order": 1,
    }
    assert retained["row"] == (None if fault in ("absent", "read_error") else raw)
    assert retained["error"] == ("RuntimeError" if fault == "read_error" else None)


@pytest.mark.asyncio
async def test_boundary_validation_without_sink_keeps_ordinary_result():
    from datetime import UTC, datetime
    from uuid import UUID

    from sqlalchemy.engine import IteratorResult
    from sqlalchemy.engine.result import SimpleResultMetaData

    from app.domain.models.playback import PlaybackBoundary
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.execution.postgres_playback import validate_playback_boundary

    boundary = PlaybackBoundary(
        run_id=UUID(int=1),
        formal_position=1,
        progress_position=0,
        observed_order=1,
        projection_revision=1,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        projector_version=1,
    )

    class Session:
        async def execute(self, statement):
            return IteratorResult(
                SimpleResultMetaData(
                    ["formal_position", "progress_position", "projection_revision", "observed_at"]
                ),
                iter([(1, 0, 1, boundary.observed_at)]),
            )

    assert (
        await validate_playback_boundary(
            Session(), boundary, trusted_scope=OwnerScope.personal("fixture")
        )
        is None
    )
