"""Pure acquisition transcripts; no database or service is opened."""

import importlib.util

import pytest


def test_pg_diagnostic_plan_preserves_loop_rounding_and_avoids_double_counting():
    assert importlib.util.find_spec("scripts.execution_capacity.pg_diagnostics_plan"), (
        "missing actual plan derivation"
    )
    from scripts.execution_capacity.pg_diagnostics_plan import read_plan

    raw = [
        {
            "Query Identifier": 72,
            "Execution Time": 2.5,
            "Plan": {
                "Node Type": "Limit",
                "Actual Rows": 2,
                "Actual Loops": 1,
                "Shared Hit Blocks": 7,
                "Shared Read Blocks": 3,
                "Plans": [
                    {
                        "Node Type": "Index Scan",
                        "Actual Rows": 2,
                        "Actual Loops": 3,
                        "Rows Removed by Filter": 1,
                        "Shared Hit Blocks": 7,
                        "Shared Read Blocks": 3,
                    }
                ],
            },
        }
    ]
    result = read_plan(raw)
    assert len(result.nodes) == 2
    assert result.nodes[1].rows.value == 6
    assert result.nodes[1].rows.precision == "estimated"
    assert result.nodes[1].rows.uncertainty == 1.5
    assert result.nodes[1].removed_filter.value == 3
    assert result.shared_hit_blocks.value == 7
    assert result.shared_read_blocks.value == 3
    assert result.scan_rows.value is None
    assert result.scan_rows.precision == "unavailable"
    assert result.duration_ns.value == 2500000


@pytest.mark.parametrize(
    "mutation",
    ["missing_rows", "negative", "nan", "missing_buffers", "missing_queryid", "not_json"],
)
def test_plan_missing_actual_evidence_never_becomes_zero(mutation):
    assert importlib.util.find_spec("scripts.execution_capacity.pg_diagnostics_plan"), (
        "missing actual plan derivation"
    )
    from scripts.execution_capacity.pg_diagnostics_plan import read_plan

    raw = [
        {
            "Query Identifier": 72,
            "Execution Time": 2.5,
            "Plan": {
                "Node Type": "Seq Scan",
                "Actual Rows": 2,
                "Actual Loops": 1,
                "Shared Hit Blocks": 7,
                "Shared Read Blocks": 3,
            },
        }
    ]
    if mutation == "missing_rows":
        del raw[0]["Plan"]["Actual Rows"]
    if mutation == "negative":
        raw[0]["Plan"]["Actual Loops"] = -1
    if mutation == "nan":
        raw[0]["Plan"]["Actual Rows"] = float("nan")
    if mutation == "missing_buffers":
        del raw[0]["Plan"]["Shared Read Blocks"]
    if mutation == "missing_queryid":
        del raw[0]["Query Identifier"]
    if mutation == "not_json":
        raw = "<plan />"
    with pytest.raises(ValueError, match=r".+"):
        read_plan(raw)


def test_fixed_repository_capture_records_actual_parameters_and_unhooks_on_failure():
    assert importlib.util.find_spec("app.infrastructure.execution.query_observation"), (
        "missing fixed repository capture"
    )
    import asyncio
    from types import SimpleNamespace
    from uuid import UUID

    from sqlalchemy import create_engine
    from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg

    from app.domain.models.playback import PlaybackBoundary
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.execution.postgres_execution_view import (
        PostgresExecutionView,
        StepStorage,
    )
    from app.infrastructure.execution.query_observation import (
        capture_queries,
        install_query_observation,
    )

    engine = create_engine("postgresql+psycopg2://unused")  # no connection opened
    conn = SimpleNamespace(
        connection=SimpleNamespace(driver_connection=SimpleNamespace(get_server_pid=lambda: 42))
    )

    class DB:
        async def execute(self, stmt, params):
            compiled = stmt.compile(dialect=PGDialect_asyncpg())
            bound = compiled.construct_params(params)
            values = tuple(bound[k] for k in compiled.positiontup)
            context = SimpleNamespace(execution_options=stmt.get_execution_options())
            engine.dispatch.before_cursor_execute(conn, None, str(compiled), values, context, False)
            engine.dispatch.after_cursor_execute(conn, None, str(compiled), values, context, False)
            return SimpleNamespace(all=list)

    boundary = PlaybackBoundary(
        run_id=UUID(int=1),
        formal_position=1,
        progress_position=0,
        observed_order=1,
        projection_revision=1,
        observed_at="2026-09-20T00:00:00Z",
        projector_version=1,
    )

    async def operation():
        with capture_queries(
            engine,
            sample_id="sample",
            action_id="action",
            clock_id="guest",
            clone_id="clone",
            expected=("steps.page",),
        ) as capture:
            await PostgresExecutionView(session_factory=None, authorization=None).page_steps(
                DB(), OwnerScope.personal("u"), boundary, StepStorage("live", "live"), {}, None, 7
            )
        return capture

    with install_query_observation(engine):
        capture = asyncio.run(operation())
    assert len(capture.statements) == 1
    stmt = capture.statements[0]
    assert stmt.repository_query == "steps.page"
    assert stmt.backend_pid == 42
    assert 7 in stmt.parameters
    assert "LIMIT" in stmt.sql
    assert stmt.ended_ns >= stmt.started_ns
    assert capture.closed
    assert not list(engine.dispatch.before_cursor_execute)


def test_capture_missing_or_duplicate_fixed_statement_is_invalid():
    assert importlib.util.find_spec("app.infrastructure.execution.query_observation"), (
        "missing fixed repository capture"
    )
    from sqlalchemy import create_engine

    from app.infrastructure.execution.query_observation import (
        capture_queries,
        install_query_observation,
    )

    engine = create_engine("postgresql+psycopg2://unused")
    with (
        install_query_observation(engine),
        capture_queries(
            engine,
            sample_id="sample",
            action_id="action",
            clock_id="guest",
            clone_id="clone",
            expected=("steps.page",),
        ) as capture,
    ):
        pass
    assert capture.errors == ["repository_statement_sequence_mismatch"]


def log_line(line, *, query_id=72, sql="SELECT target($1)", pid=42, **extra):
    import json

    plan = {
        "Query Identifier": query_id,
        "Query Text": sql,
        "Plan": {
            "Node Type": "Result",
            "Actual Rows": 1,
            "Actual Loops": 1,
            "Shared Hit Blocks": 2,
            "Shared Read Blocks": 0,
            "Output": ["private-signed-body"],
        },
    }
    return (
        json.dumps(
            dict(
                pid=pid,
                session_id="abc.2a",
                line_num=line,
                txid=37,
                query_id=72,
                message="duration: 1.250 ms  plan:\n" + json.dumps(plan),
                **extra,
            )
        )
        + "\n"
    ).encode()


def test_pg16_nested_log_binding_retains_safe_plans_not_signed_expressions():
    assert importlib.util.find_spec("scripts.execution_capacity.pg_diagnostics_log"), (
        "missing actual nested trace acquisition"
    )
    from scripts.execution_capacity.pg_diagnostics_log import parse_trace

    top, nested = parse_trace(
        log_line(4, query_id=91, sql="SELECT nested(private)") + log_line(5),
        pid=42,
        session_id="abc.2a",
        txid=37,
        sql="SELECT target($1)",
    )
    assert top.query_id == "72"
    assert nested[0].query_id == "91"
    assert top.duration_ns.value == 1250000
    assert "private" not in top.model_dump_json()


@pytest.mark.parametrize(
    "mutation",
    [
        "partial",
        "gap",
        "wrong_transaction",
        "wrong_session",
        "wrapper_missing",
        "parallel",
        "duplicate_top",
    ],
)
def test_pg16_nested_logs_reject_ambiguous_or_incomplete_trace(mutation):
    assert importlib.util.find_spec("scripts.execution_capacity.pg_diagnostics_log"), (
        "missing actual nested trace acquisition"
    )
    from scripts.execution_capacity.pg_diagnostics_log import parse_trace

    raw = log_line(4, query_id=91, sql="SELECT nested(private)") + log_line(5)
    if mutation == "partial":
        raw = raw[:-1]
    if mutation == "gap":
        raw = log_line(3, query_id=91, sql="SELECT nested(private)") + log_line(5)
    if mutation == "wrong_transaction":
        raw = raw.replace(b'"txid": 37', b'"txid": 38')
    if mutation == "wrong_session":
        raw = raw.replace(b"abc.2a", b"bad.2a")
    if mutation == "wrapper_missing":
        raw = log_line(4, query_id=91, sql="SELECT nested(private)")
    if mutation == "parallel":
        raw += log_line(6, pid=57, leader_pid=42)
    if mutation == "duplicate_top":
        raw += log_line(6)
    with pytest.raises(ValueError, match=r".+"):
        parse_trace(raw, pid=42, session_id="abc.2a", txid=37, sql="SELECT target($1)")


def test_private_log_range_detects_rotation_truncation_and_incomplete_append(tmp_path):
    assert importlib.util.find_spec("scripts.execution_capacity.pg_diagnostics_log"), (
        "missing actual nested trace acquisition"
    )
    from scripts.execution_capacity.pg_diagnostics_log import LogRange

    path = tmp_path / "postgres.json"
    path.write_bytes(b"old line\n")
    mark = LogRange.start(path)
    with path.open("ab") as stream:
        stream.write(log_line(1))
    assert mark.finish(path) == log_line(1)
    path.write_bytes(b"changed\n")
    with pytest.raises(ValueError, match=r".+"):
        mark.finish(path)


@pytest.mark.parametrize(
    "mutation", [None, "reset", "deallocate", "two_calls", "wrong_query", "decrease"]
)
def test_actual_stat_delta_requires_unique_same_query_call_without_reset(mutation):
    assert importlib.util.find_spec("scripts.execution_capacity.pg_diagnostics_sql"), (
        "missing actual PG acquisition"
    )
    from scripts.execution_capacity.pg_diagnostics_sql import attributed_statistics

    before = {
        "reset": "2026-09-20",
        "dealloc": 0,
        "rows": [
            {
                "queryid": 72,
                "calls": 2,
                "rows": 4,
                "total_exec_time": 10.0,
                "shared_blks_hit": 3,
                "shared_blks_read": 1,
            }
        ],
    }
    after = {
        "reset": "2026-09-20",
        "dealloc": 0,
        "rows": [
            {
                "queryid": 72,
                "calls": 3,
                "rows": 6,
                "total_exec_time": 12.5,
                "shared_blks_hit": 10,
                "shared_blks_read": 4,
            }
        ],
    }
    if mutation == "reset":
        after["reset"] = "other"
    if mutation == "deallocate":
        after["dealloc"] = 1
    if mutation == "two_calls":
        after["rows"][0]["calls"] = 4
    if mutation == "wrong_query":
        after["rows"][0]["queryid"] = 99
    if mutation == "decrease":
        after["rows"][0]["shared_blks_hit"] = 1
    if mutation:
        with pytest.raises(ValueError, match=r".+"):
            attributed_statistics(before, after, "72", database_id=7, user_id=9)
    else:
        first, last = attributed_statistics(before, after, "72", database_id=7, user_id=9)
        assert last.rows - first.rows == 2
        assert last.shared_read_blocks - first.shared_read_blocks == 3


def test_wait_precision_does_not_invent_exact_zero_between_snapshots():
    assert importlib.util.find_spec("scripts.execution_capacity.pg_diagnostics_sql"), (
        "missing actual PG acquisition"
    )
    from scripts.acceptance.capacity_diagnostics import WaitObservation
    from scripts.execution_capacity.pg_diagnostics_sql import wait_quantity

    rows = [
        WaitObservation(
            started_ns=10,
            ended_ns=12,
            backend_pid=42,
            query_id="72",
            waiting_for_lock=False,
            blockers=[],
            ungranted_locks=0,
            raw_digest="a" * 64,
        ),
        WaitObservation(
            started_ns=20,
            ended_ns=22,
            backend_pid=42,
            query_id="72",
            waiting_for_lock=False,
            blockers=[],
            ungranted_locks=0,
            raw_digest="b" * 64,
        ),
    ]
    value = wait_quantity(rows, 10, 30)
    assert value.precision == "estimated"
    assert value.value == 0
    assert value.uncertainty == 20
    assert wait_quantity([], 10, 30).precision == "unavailable"


def gate_fixture():
    from types import SimpleNamespace

    identity = {
        "sample_id": "sample",
        "action_id": "action",
        "window_id": "window",
        "round_id": "round",
        "boot_id": "boot",
        "clone_id": "clone",
        "observer_clock_id": "guest",
    }
    request = {
        "command_id": "command",
        "identity": identity,
        "host_ns": 120,
        "clock_id": "host",
        "sample_end_ns": 100,
        "operation": "history",
    }
    ledger = SimpleNamespace(
        records=lambda kind: [{"body": request}] if kind == "pg-diagnostics-dispatch" else []
    )
    plan = SimpleNamespace(
        sample_id="sample", action_id="action", physical_window_id="window", operation="history"
    )
    sample = SimpleNamespace(sample_id="sample", end_ns=100, clock_id="host")
    origin = SimpleNamespace(
        kind="round",
        boot_id="boot",
        clone_id="clone",
        round=SimpleNamespace(round_id="round", sample_id="sample", window_id="window"),
    )
    return ledger, request, plan, sample, origin


@pytest.mark.parametrize(
    "mutation", [None, "prewarm", "clock", "clone", "sample", "boot", "no_dispatch"]
)
def test_diagnostic_gate_uses_actual_host_dispatch_and_exact_guest_identity(mutation):
    assert importlib.util.find_spec("scripts.execution_capacity.pg_diagnostics"), (
        "missing actual diagnostic collector"
    )
    from scripts.execution_capacity.pg_diagnostics import authorize_request

    ledger, request, plan, sample, origin = gate_fixture()
    if mutation == "prewarm":
        request["host_ns"] = 99
    if mutation == "clock":
        request["clock_id"] = "guest"
    if mutation in {"clone", "sample", "boot"}:
        request["identity"][mutation + "_id"] = "wrong"
    if mutation == "no_dispatch":
        ledger.records = lambda kind: []
    if mutation:
        with pytest.raises(ValueError, match=r".+"):
            authorize_request(ledger, plan, sample, origin, command_id="command")
    else:
        gate = authorize_request(ledger, plan, sample, origin, command_id="command")
        assert gate.identity["observer_clock_id"] == "guest"
        assert gate.dispatched_ns == 120


def acquisition_fixture(tmp_path, monkeypatch, mutation=None):
    import asyncio

    from scripts.execution_capacity import pg_diagnostics_sql as sql
    from scripts.execution_capacity.inventory import SourceInventory

    from app.infrastructure.execution.query_observation import CapturedStatement, QueryCapture

    path = tmp_path / "postgres.json"
    path.write_bytes(b"")
    identity = {
        "database_name": "capacity",
        "database_user": "reader",
        "database_system_identifier": "system",
        "migrations": ["0012"],
        "server_version": "160004",
        "database_id": 7,
        "user_id": 9,
        "pid": 42,
        "backend_start": "2026-09-20 00:00:00+00",
        "session_id": "abc.2a",
        "txid": 37,
        "logfile": "postgres.json",
    }
    caps = {
        "extension_schema": "public",
        "extension_version": "1.10",
        "track": "top",
        "compute_query_id": "on",
        "log_min_duration": "0",
        "log_analyze": "on",
        "log_buffers": "on",
        "log_verbose": "on",
        "log_nested_statements": "on",
        "sample_rate": "1",
        "log_format": "json",
        "log_parameter_max_length": "0",
        "logging_collector": "on",
        "log_destination": "jsonlog",
        "columns": [
            "userid",
            "dbid",
            "toplevel",
            "queryid",
            "calls",
            "rows",
            "total_exec_time",
            "shared_blks_hit",
            "shared_blks_read",
        ],
    }
    if mutation == "capability":
        caps["log_analyze"] = "off"
    if mutation == "version":
        identity["server_version"] = "170000"
    calls, rollbacks = [], []

    class Result:
        def __init__(self, value):
            self.value = value

        def mappings(self):
            return self

        def one(self):
            return self.value

        def close(self):
            pass

    class Transaction:
        async def rollback(self):
            rollbacks.append(True)

    class Connection:
        sync_connection = object()

        async def execute(self, statement):
            query = str(statement)
            if query == sql.IDENTITY:
                return Result(identity)
            if query == sql.CAPABILITIES:
                return Result(caps)
            raise AssertionError("unexpected SQL boundary " + query)

        async def begin_nested(self):
            return Transaction()

        async def exec_driver_sql(self, statement, parameters):
            assert statement == "SELECT target($1)"
            assert parameters == ("private-signed-body",)
            calls.append(statement)
            await asyncio.sleep(0)
            if mutation == "execution":
                raise RuntimeError("sensitive database error private-signed-body")
            with path.open("ab") as stream:
                stream.write(log_line(5))
            return Result([])

    connection = Connection()

    class Observer:
        async def fetchval(self, query):
            assert query == "SELECT pg_current_logfile('jsonlog')"
            return "postgres.json"

        def is_in_transaction(self):
            return False

        async def fetchrow(self, query, *args):
            if query == sql.IDENTITY:
                return {**identity, "pid": 99}
            if query == sql.STATS_INFO:
                return {
                    "reset": "different" if mutation == "reset" and calls else "2026-09-20",
                    "dealloc": 0,
                }
            if query == sql.WAIT:
                assert args == (42,)
                return {
                    "pid": 42,
                    "backend_start": identity["backend_start"],
                    "query_id": "72",
                    "wait_event_type": None,
                    "wait_event": None,
                    "state": "active",
                    "blockers": [],
                    "ungranted_locks": 0,
                    "locks": [],
                }
            raise AssertionError("unexpected observer SQL " + query)

        async def fetch(self, query, *args):
            assert query == sql.STATS
            assert args == (7, 9)
            return [
                {
                    "queryid": 72,
                    "calls": 2 + len(calls),
                    "rows": 4 + len(calls),
                    "total_exec_time": 10.0 + len(calls),
                    "shared_blks_hit": 3 + len(calls),
                    "shared_blks_read": 1,
                }
            ]

    capture = QueryCapture("sample", "action", "guest", "clone", ("steps.page",))
    capture.statements = [
        CapturedStatement(
            "steps.page",
            "SELECT target($1)",
            ("private-signed-body",),
            42,
            1,
            2,
            connection.sync_connection,
        )
    ]
    capture.closed = True
    inventory = SourceInventory(
        database={
            k: identity[k]
            for k in (
                "database_name",
                "database_user",
                "database_system_identifier",
                "migrations",
                "server_version",
            )
        },
        build={"digest": "c" * 64, "groups": {}},
        reads_complete=True,
    )
    if mutation == "identity":
        inventory.database["database_system_identifier"] = "other"
    monkeypatch.setattr(
        "scripts.execution_capacity.pg_diagnostics_collect.read_build_inventory",
        lambda *a: inventory.build,
    )
    ledger, _request, plan, sample, origin = gate_fixture()
    from scripts.execution_capacity.pg_diagnostics import authorize_request

    gate = authorize_request(ledger, plan, sample, origin, command_id="command")
    return connection, Observer(), capture, inventory, gate, path, calls, rollbacks


@pytest.mark.parametrize(
    "mutation", [None, "capability", "version", "identity", "execution", "reset"]
)
def test_actual_collector_acquires_bound_evidence_or_retains_sanitized_failure(
    tmp_path, monkeypatch, mutation
):
    assert importlib.util.find_spec("scripts.execution_capacity.pg_diagnostics_collect"), (
        "missing actual replay/observer acquisition"
    )
    import asyncio

    from scripts.execution_capacity.pg_diagnostics_collect import collect

    conn, observer, capture, inventory, gate, _path, calls, rollbacks = acquisition_fixture(
        tmp_path, monkeypatch, mutation
    )
    result = asyncio.run(
        collect(capture, conn, observer, inventory, gate, build_root=tmp_path, log_root=tmp_path)
    )
    if mutation in {"capability", "version", "identity"}:
        assert not calls
        assert result.errors
    elif mutation:
        assert len(calls) == 1
        assert result.statements[0].errors
        assert rollbacks == [True]
    else:
        assert not result.errors
        assert len(result.statements) == 1
        statement = result.statements[0]
        assert not statement.errors
        assert statement.before.calls == 2
        assert statement.after.calls == 3
        assert statement.plan.nodes[0].rows.value == 1
        assert statement.lock_wait_ns.precision == "estimated"
        assert statement.instrumentation_ns.precision == "unavailable"
        assert rollbacks == [True]
    assert "private-signed-body" not in str(result.payload())
    repeated = asyncio.run(
        collect(capture, conn, observer, inventory, gate, build_root=tmp_path, log_root=tmp_path)
    )
    assert repeated.errors


def test_safe_export_requires_actual_response_digest_and_shared_consumer_rejects_missing_authority(
    tmp_path, monkeypatch
):
    import asyncio

    from scripts.acceptance.capacity_diagnostics import validate_query
    from scripts.acceptance.capacity_io import canonical_digest
    from scripts.execution_capacity.pg_diagnostics_collect import collect

    conn, observer, capture, inventory, gate, _path, _calls, _rollbacks = acquisition_fixture(
        tmp_path, monkeypatch
    )
    result = asyncio.run(
        collect(capture, conn, observer, inventory, gate, build_root=tmp_path, log_root=tmp_path)
    )
    ledger, request, plan, sample, origin = gate_fixture()
    with pytest.raises(ValueError, match=r".+"):
        result.export(ledger)
    receipt = {
        "command_id": "command",
        "identity": gate.identity,
        "clock_id": "host",
        "host_ns": 140,
        "request_digest": gate.request_digest,
        "payload_digest": canonical_digest(result.payload()),
    }
    ledger.records = lambda kind: [
        {"body": request if kind == "pg-diagnostics-dispatch" else receipt}
    ]
    exported = result.export(ledger)
    assert exported.collected_ns == 120
    assert exported.ended_ns == 140
    assert exported.observer_started_ns > 140  # unrelated guest epoch is not subtracted
    validate_query(
        exported, plan, sample, clock_id="host", origin=origin, source=source_for(exported)
    )
    receipt["payload_digest"] = "a" * 64
    with pytest.raises(ValueError, match=r".+"):
        result.export(ledger)
    for change in [
        {"clock_id": "guest"},
        {"clone_id": "other"},
        {"boot_id": "other"},
        {"errors": ["missing"]},
        {"collected_ns": 99},
        {"capture_digest": "e" * 64},
    ]:
        with pytest.raises(ValueError, match=r".+"):
            validate_query(
                exported.model_copy(update=change),
                plan,
                sample,
                clock_id="host",
                origin=origin,
                source=source_for(exported),
            )


def original_timed_inputs(
    tmp_path,
    monkeypatch,
    mutation=None,
    *,
    inventory_override=None,
    budget=None,
    parameters=("private-signed-body",),
):
    assert importlib.util.find_spec("scripts.execution_capacity.pg_diagnostics_timed"), (
        "missing original timed trace collector"
    )
    import asyncio
    from types import SimpleNamespace

    from scripts.execution_capacity import pg_diagnostics_sql as sql
    from scripts.execution_capacity.pg_diagnostics_timed import TimedMetadata
    from sqlalchemy import create_engine

    from app.infrastructure.execution.query_observation import (
        capture_queries,
        install_query_observation,
    )

    conn, observer, _old, inventory, gate, path, _calls, _rollbacks = acquisition_fixture(
        tmp_path, monkeypatch
    )
    identity = asyncio.run(conn.execute(sql.IDENTITY)).one()
    caps = asyncio.run(conn.execute(sql.CAPABILITIES)).one()
    if inventory_override is not None:
        inventory = inventory_override
        identity.update(inventory.database)
    engine = create_engine("postgresql+psycopg2://unused")
    step = [0]
    lines = [0]

    class Result:
        def __init__(self, value):
            self.value = value

        def mappings(self):
            return self

        def one(self):
            return self.value

        def all(self):
            return self.value

        def close(self):
            pass

    class Sync:
        connection = SimpleNamespace(driver_connection=SimpleNamespace(get_server_pid=lambda: 42))

        def exec_driver_sql(self, statement, parameters=()):
            if statement == sql.IDENTITY:
                return Result(identity)
            if statement == sql.CAPABILITIES:
                return Result(caps)
            if statement == sql.STATS_INFO:
                return Result(
                    {
                        "reset": "changed" if mutation == "reset" and step[0] else "2026-09-20",
                        "dealloc": (
                            1
                            if mutation == "deallocate" and step[0]
                            else -1
                            if mutation == "invalid_dealloc"
                            else 0
                        ),
                    }
                    | ({"reset": ""} if mutation == "invalid_reset" else {})
                )
            if statement == sql.STATS:
                row = {
                    "queryid": 72,
                    "calls": 2 + step[0] * (2 if mutation == "concurrent" else 1),
                    "rows": 4 + step[0],
                    "total_exec_time": 10.0 + step[0],
                    "shared_blks_hit": 3 + step[0],
                    "shared_blks_read": 1,
                }
                changes = {
                    "decreased_rows": ("rows", 1),
                    "decreased_time": ("total_exec_time", 1.0),
                    "decreased_hits": ("shared_blks_hit", 1),
                    "decreased_reads": ("shared_blks_read", 0),
                    "decreased_calls": ("calls", 1),
                    "negative": ("rows", -1),
                    "malformed": ("calls", "3"),
                    "missing_and_malformed": ("rows", "bad"),
                    "other_malformed": ("rows", "bad"),
                }
                if step[0] and mutation in changes:
                    key, value = changes[mutation]
                    row[key] = value
                if (mutation in {"missing_before", "missing_and_malformed"} and not step[0]) or (
                    mutation == "missing_after" and step[0]
                ):
                    return Result([])
                if mutation == "other_malformed":
                    row["queryid"] = 99
                return Result([row])
            if statement == sql.WAIT:
                return Result(
                    {
                        "pid": 42,
                        "backend_start": identity["backend_start"],
                        "query_id": "72",
                        "wait_event_type": None,
                        "wait_event": None,
                        "state": "idle",
                        "blockers": [],
                        "ungranted_locks": 0,
                        "locks": [],
                    }
                )
            assert statement.startswith("SELECT 1 /* opencitadel-diagnostic-")
            lines[0] += 1
            with path.open("ab") as stream:
                if not (mutation == "missing_anchor" and "-end */" in statement):
                    stream.write(log_line(lines[0], sql=statement))
            return Result([])

    from scripts.execution_capacity.evidence_bounds import EvidenceBudget

    metadata = TimedMetadata(
        inventory,
        build_root=tmp_path,
        log_root=tmp_path,
        budget=budget if budget is not None else EvidenceBudget(),
    )
    monkeypatch.setattr(
        "scripts.execution_capacity.pg_diagnostics_timed.read_build_inventory",
        lambda *a: inventory.build,
    )
    sync = Sync()
    context = SimpleNamespace(execution_options={"execution_repository_query": "analysis.capture"})
    with (
        install_query_observation(engine),
        capture_queries(
            engine,
            sample_id="sample",
            action_id="action",
            clock_id="guest",
            clone_id="clone",
            expected=("analysis.capture",),
            _observer=metadata,
        ) as capture,
    ):
        engine.dispatch.before_cursor_execute(
            sync, None, "SELECT target($1)", parameters, context, False
        )
        lines[0] += 1
        with path.open("ab") as stream:
            stream.write(log_line(lines[0], query_id=91, sql="SELECT nested(private)"))
        lines[0] += 1
        with path.open("ab") as stream:
            stream.write(log_line(lines[0]))
        step[0] = 1
        engine.dispatch.after_cursor_execute(
            sync, None, "SELECT target($1)", parameters, context, False
        )
    # Simulates the real request UOW releasing its connection. Collection cannot
    # invoke SQL on it; only the independent observer and private log are used.
    sync.exec_driver_sql = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("closed timed backend reused")
    )
    if mutation == "wrong_session":
        path.write_bytes(path.read_bytes().replace(b"abc.2a", b"bad.2a"))
    return capture, metadata, observer, gate, inventory


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "concurrent",
        "missing_before",
        "missing_after",
        "reset",
        "deallocate",
        "missing_anchor",
        "wrong_session",
        "decreased_rows",
        "decreased_time",
        "decreased_hits",
        "decreased_reads",
        "decreased_calls",
        "negative",
        "malformed",
        "invalid_reset",
        "invalid_dealloc",
        "missing_and_malformed",
        "other_malformed",
    ],
)
def test_original_timed_trace_collects_after_connection_closed_without_replay(
    tmp_path, monkeypatch, mutation
):
    import asyncio

    from scripts.execution_capacity.pg_diagnostics_timed import collect_original

    capture, metadata, observer, gate, inventory = original_timed_inputs(
        tmp_path, monkeypatch, mutation
    )
    result = asyncio.run(collect_original(capture, metadata, observer, gate))
    assert not result.errors
    invalid = mutation not in {None, "concurrent", "missing_before", "missing_after"}
    if invalid:
        assert result.statements[0].errors
        assert result.private["preparations"][0]["before"]
        assert result.private["preparations"][0]["after"]
    else:
        assert not result.statements[0].errors
        assert result.statements[0].plan_origin == "original-timed-execution"
        assert result.statements[0].nested_plans[0].query_id == "91"
        if mutation in {"concurrent", "missing_before", "missing_after"}:
            assert result.statements[0].before is None
            assert result.statements[0].statistics_attribution == "aggregate-only"
        else:
            assert result.statements[0].before.calls == 2
        assert result.buffer_origin == "original-timed-execution"
        assert "private-signed-body" not in str(result.payload(budget=metadata.budget))

    from scripts.acceptance.capacity_diagnostics import validate_query
    from scripts.acceptance.capacity_io import canonical_digest

    ledger, request, plan, sample, origin = gate_fixture()
    receipt = {
        "command_id": "command",
        "identity": gate.identity,
        "clock_id": "host",
        "host_ns": 140,
        "request_digest": gate.request_digest,
        "payload_digest": canonical_digest(result.payload(budget=metadata.budget)),
    }
    ledger.records = lambda kind: [
        {"body": request if kind == "pg-diagnostics-dispatch" else receipt}
    ]
    exported = result.export(ledger, budget=metadata.budget)
    if invalid:
        with pytest.raises(ValueError, match="incomplete diagnostic statement evidence"):
            validate_query(
                exported, plan, sample, clock_id="host", origin=origin, source=source_for(exported)
            )
        return

    validate_query(
        exported, plan, sample, clock_id="host", origin=origin, source=source_for(exported)
    )
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget
    from scripts.execution_capacity.evidence_owner import copy_original
    from scripts.execution_capacity.pg_diagnostics_timed import replay_original

    replay_budget = EvidenceBudget()
    retained = copy_original(result, budget=replay_budget)
    replayed = replay_original(retained, inventory, budget=replay_budget)
    assert replayed.payload(budget=replay_budget) == result.payload(budget=metadata.budget)
    assert replayed.export(ledger, budget=replay_budget) == exported
    for field, value in [
        ("parameters_digest", "d" * 64),
        ("trace_binding", None),
        (
            "waits",
            [w.model_copy(update={"backend_pid": 999}) for w in exported.statements[0].waits],
        ),
        (
            "lock_wait_ns",
            exported.statements[0].lock_wait_ns.model_copy(
                update={"precision": "measured", "uncertainty": 0}
            ),
        ),
    ]:
        changed = exported.model_copy(deep=True)
        setattr(changed.statements[0], field, value)
        with pytest.raises(ValueError, match=r".+"):
            validate_query(
                changed, plan, sample, clock_id="host", origin=origin, source=source_for(exported)
            )


def test_shared_diagnostics_role_rejects_legacy_exact_aggregate_quantities():
    from scripts.acceptance.capacity_models import Diagnostics

    legacy = {
        "sample_id": "sample",
        "clock_id": "host",
        "collected_ns": 100,
        "clone_id": "clone",
        "query_id": "72",
        "collection": "pg_stat_statements+explain-analyze-buffers+pg_locks",
        "scan_rows": 10,
        "loops": 1,
        "rows_removed": 0,
        "shared_hit_blocks": 10,
        "shared_read_blocks": 0,
        "lock_wait_ns": 0,
        "duration_ns": 10,
        "instrumentation_ns": 0,
    }
    with pytest.raises(ValueError, match=r".+"):
        Diagnostics(attempt_id="attempt", protocol_id="protocol", queries=[legacy])


def source_for(query):
    from types import SimpleNamespace

    return SimpleNamespace(
        repository_capture_digest=query.capture_digest,
        repository_clock_id=query.observer_clock_id,
        database_identity_digest=query.database_digest,
        build_inventory_digest=query.build_digest,
    )


def test_analysis_supplementary_queries_are_captured_at_actual_repository_boundary(monkeypatch):
    import asyncio

    from app.infrastructure.repositories.db_analysis_charts import chart_facts
    from app.infrastructure.repositories.db_analysis_points import points_operation
    from app.infrastructure.repositories.db_current_authority import DBCurrentAuthority

    statements = []

    class DB:
        async def scalar(self, statement, parameters):
            statements.append((statement.get_execution_options(), parameters))
            return {"actual": True}

    async def signed(self, *args, **kwargs):
        return {"body": "private-signed-body", "signature": "private-signature"}

    monkeypatch.setattr(DBCurrentAuthority, "signed", signed)
    db = DB()
    assert asyncio.run(
        chart_facts(
            db, None, None, signing_secret="private", operation="live", capture_id="capture"
        )
    ) == {"actual": True}
    assert asyncio.run(
        points_operation(
            db,
            None,
            None,
            secret="private",
            kind="analysis",
            capture="capture",
            operation="prepare",
        )
    ) == {"actual": True}
    assert [s[0].get("execution_repository_query") for s in statements] == [
        "analysis.charts",
        "analysis.points",
    ]
    assert all(s[1]["signature"] == "private-signature" for s in statements)


def test_unsigned_explain_identifier_and_signed_pgss_identifier_are_same_query():
    from scripts.execution_capacity.pg_diagnostics_plan import read_plan

    raw = [
        {
            "Query Identifier": 2**64 - 3,
            "Execution Time": 1,
            "Plan": {
                "Node Type": "Result",
                "Actual Rows": 1,
                "Actual Loops": 1,
                "Shared Hit Blocks": 0,
                "Shared Read Blocks": 0,
            },
        }
    ]
    assert read_plan(raw).query_id == "-3"


def test_analysis_scalar_evaluation_snapshot_has_fixed_query_identity():
    import asyncio
    from types import SimpleNamespace

    from app.domain.models.scope import OwnerScope, Principal
    from app.infrastructure.repositories.db_evaluation_summary_repository import (
        DBEvaluationSummaryRepository,
    )

    statements = []

    class DB:
        async def scalar(self, statement, parameters=None):
            if parameters is None:
                return "actual-authorization-signature"
            statements.append(statement.get_execution_options())
            return {"actual": True}

    class Authorization:
        async def authorize(self, scope, principal, *, write):
            assert scope.user_id == principal.user_id
            assert not write

    work = SimpleNamespace(db_session=DB(), evaluation_dataset=Authorization())
    result = asyncio.run(
        DBEvaluationSummaryRepository(work, signing_secret="private")._read(
            OwnerScope.personal("user"), Principal(user_id="user"), "batch", operation="capture"
        )
    )
    assert result == {"actual": True}
    assert statements[0].get("execution_repository_query") == "analysis.scores"


def test_plan_reported_duration_rounding_and_parallel_worker_facts_are_preserved():
    from scripts.execution_capacity.pg_diagnostics_plan import read_plan

    raw = [
        {
            "Query Identifier": 7,
            "Execution Time": 1.234,
            "Plan": {
                "Node Type": "Gather",
                "Actual Rows": 10,
                "Actual Loops": 1,
                "Shared Hit Blocks": 3,
                "Shared Read Blocks": 1,
                "Workers Planned": 2,
                "Workers Launched": 1,
                "Plans": [
                    {
                        "Node Type": "Seq Scan",
                        "Parallel Aware": True,
                        "Actual Rows": 5,
                        "Actual Loops": 2,
                        "Shared Hit Blocks": 3,
                        "Shared Read Blocks": 1,
                        "Workers": [
                            {
                                "Worker Number": 0,
                                "Actual Rows": 4,
                                "Actual Loops": 1,
                                "Shared Hit Blocks": 2,
                                "Shared Read Blocks": 1,
                            }
                        ],
                    }
                ],
            },
        }
    ]
    plan = read_plan(raw)
    assert plan.duration_ns.precision == "estimated"
    assert plan.duration_ns.uncertainty == 500
    assert plan.nodes[0].workers_launched == 1
    worker = plan.nodes[1].workers[0]
    assert worker.worker_number == 0
    assert worker.rows.value == 4
    assert worker.shared_hit_blocks.value == 2


def test_post_timing_replay_never_reuses_signed_analysis_authority(tmp_path, monkeypatch):
    import asyncio

    from scripts.execution_capacity.pg_diagnostics_collect import collect

    conn, observer, capture, inventory, gate, _path, calls, _rollbacks = acquisition_fixture(
        tmp_path, monkeypatch
    )
    capture.statements[0].repository_query = "analysis.capture"
    capture.expected = ("analysis.capture",)
    result = asyncio.run(
        collect(capture, conn, observer, inventory, gate, build_root=tmp_path, log_root=tmp_path)
    )
    assert result.errors
    assert calls == []


def test_shared_plan_rejects_missing_node_rows():
    from scripts.acceptance.capacity_diagnostics import validate_plan
    from scripts.execution_capacity.pg_diagnostics_plan import read_plan

    plan = read_plan(
        [
            {
                "Query Identifier": 7,
                "Execution Time": 1,
                "Plan": {
                    "Node Type": "Result",
                    "Actual Rows": 1,
                    "Actual Loops": 1,
                    "Shared Hit Blocks": 0,
                    "Shared Read Blocks": 0,
                },
            }
        ]
    )
    plan.nodes[0].rows = plan.scan_rows
    with pytest.raises(ValueError, match="rows"):
        validate_plan(plan)


@pytest.mark.parametrize("pause_at", ["before", "after"])
def test_overlapping_greenlet_observers_keep_capture_ownership_and_listener_stability(pause_at):
    import asyncio
    from types import SimpleNamespace

    from sqlalchemy import create_engine
    from sqlalchemy.util.concurrency import await_only, greenlet_spawn

    from app.infrastructure.execution.query_observation import (
        capture_queries,
        install_query_observation,
    )

    engine = create_engine("postgresql+psycopg2://unused")  # never connects
    conn = SimpleNamespace(
        connection=SimpleNamespace(driver_connection=SimpleNamespace(get_server_pid=lambda: 42))
    )

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Observer:
            def before(self, connection, name):
                if pause_at == "before":
                    entered.set()
                    await_only(release.wait())
                return {"observed": name}

            def after(self, connection, statement):
                if pause_at == "after":
                    entered.set()
                    await_only(release.wait())

        async def operation(sample, observer=None):
            context = SimpleNamespace(
                execution_options={"execution_repository_query": "steps.page"}
            )
            with capture_queries(
                engine,
                sample_id=sample,
                action_id="action",
                clock_id="guest",
                clone_id="clone",
                expected=("steps.page",),
                _observer=observer,
            ) as capture:
                await greenlet_spawn(
                    engine.dispatch.before_cursor_execute,
                    conn,
                    None,
                    "SELECT target($1)",
                    (sample,),
                    context,
                    False,
                )
                await greenlet_spawn(
                    engine.dispatch.after_cursor_execute,
                    conn,
                    None,
                    "SELECT target($1)",
                    (sample,),
                    context,
                    False,
                )
            return capture

        first = asyncio.create_task(operation("first", Observer()))
        await entered.wait()
        # Teardown cannot mutate the suspended dispatcher. Its refusal leaves
        # the installation alive while the other task completes independently.
        with pytest.raises(ValueError, match="drain"):
            facility.close()
        second = await operation("second")
        release.set()
        return await first, second

    with install_query_observation(engine) as facility:
        first, second = asyncio.run(scenario())
    assert not first.errors
    assert not second.errors
    assert [s.parameters for s in first.statements] == [("first",)]
    assert [s.parameters for s in second.statements] == [("second",)]
    assert first.statements[0].ended_ns >= first.statements[0].started_ns


def test_capture_requires_quiescent_install_and_drained_teardown():
    from sqlalchemy import create_engine

    from app.infrastructure.execution.query_observation import (
        capture_queries,
        install_query_observation,
    )

    engine = create_engine("postgresql+psycopg2://unused")
    args = {
        "sample_id": "sample",
        "action_id": "action",
        "clock_id": "guest",
        "clone_id": "clone",
        "expected": ("steps.page",),
    }
    with pytest.raises(ValueError, match="installed"), capture_queries(engine, **args):
        pass
    facility = install_query_observation(engine)
    with pytest.raises(ValueError, match="already installed"):
        install_query_observation(engine)
    with capture_queries(engine, **args), pytest.raises(ValueError, match="drain"):
        facility.close()
    facility.close()
    assert not list(engine.dispatch.before_cursor_execute)
    assert not list(engine.dispatch.after_cursor_execute)
    with pytest.raises(ValueError, match="installed"), capture_queries(engine, **args):
        pass


def test_inherited_child_context_cannot_write_parent_capture():
    import asyncio
    from types import SimpleNamespace

    from sqlalchemy import create_engine
    from sqlalchemy.util.concurrency import greenlet_spawn

    from app.infrastructure.execution.query_observation import (
        capture_queries,
        install_query_observation,
    )

    engine = create_engine("postgresql+psycopg2://unused")
    conn = SimpleNamespace(
        connection=SimpleNamespace(driver_connection=SimpleNamespace(get_server_pid=lambda: 42))
    )

    async def dispatch(value):
        context = SimpleNamespace(execution_options={"execution_repository_query": "steps.page"})
        await greenlet_spawn(
            engine.dispatch.before_cursor_execute,
            conn,
            None,
            "SELECT target($1)",
            (value,),
            context,
            False,
        )
        await greenlet_spawn(
            engine.dispatch.after_cursor_execute,
            conn,
            None,
            "SELECT target($1)",
            (value,),
            context,
            False,
        )

    async def scenario():
        with capture_queries(
            engine,
            sample_id="sample",
            action_id="action",
            clock_id="guest",
            clone_id="clone",
            expected=("steps.page",),
        ) as capture:
            await dispatch("parent")
            await asyncio.create_task(dispatch("child"))
        return capture

    with install_query_observation(engine):
        capture = asyncio.run(scenario())
    assert [s.parameters for s in capture.statements] == [("parent",)]
    assert not capture.errors
