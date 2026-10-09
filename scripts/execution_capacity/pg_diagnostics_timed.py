"""Original timed PG16 plan capture; actual plans are read only after timing.

Metadata-only preparation never executes target reads/EXPLAIN/capture/read cuts.
Preinstalled auto_explain's actual cost and all observer metadata stay in native
latency. Nothing is subtracted. Original HTTP connections can close normally.
"""

import time
from uuid import uuid4

from scripts.acceptance.capacity_diagnostics import (
    StatementDiagnostic,
    TraceBinding,
    WaitObservation,
)
from scripts.acceptance.capacity_io import canonical_digest, strict_json
from scripts.execution_capacity import pg_diagnostics_sql as sql
from scripts.execution_capacity.inventory import read_build_inventory
from scripts.execution_capacity.pg_diagnostics import DiagnosticResult
from scripts.execution_capacity.pg_diagnostics_collect import log_path, plain
from scripts.execution_capacity.pg_diagnostics_log import LogRange, parse_trace
from scripts.execution_capacity.pg_diagnostics_plan import unavailable


def sync_row(connection, statement, parameters=()):
    return dict(connection.exec_driver_sql(statement, parameters).mappings().one())


def sync_statistics(connection, identity):
    first = sync_row(connection, sql.STATS_INFO)
    rows = [
        dict(r)
        for r in connection.exec_driver_sql(
            sql.STATS, (identity["database_id"], identity["user_id"])
        )
        .mappings()
        .all()
    ]
    last = sync_row(connection, sql.STATS_INFO)
    if first != last or len(rows) > 10000 or len({r["queryid"] for r in rows}) != len(rows):
        raise ValueError("incomplete/reset statistics snapshot")
    return {**first, "rows": rows}


def boundary_wait(connection, identity):
    started = time.monotonic_ns()
    raw = sync_row(connection, sql.WAIT, (identity["pid"],))
    ended = time.monotonic_ns()
    if raw["pid"] != identity["pid"] or raw["backend_start"] != identity["backend_start"]:
        raise ValueError("diagnostic backend identity changed")
    return raw, WaitObservation(
        started_ns=started,
        ended_ns=ended,
        backend_pid=identity["pid"],
        query_id=raw["query_id"],
        waiting_for_lock=raw["wait_event_type"] == "Lock",
        blockers=raw["blockers"],
        ungranted_locks=raw["ungranted_locks"],
        raw_digest=canonical_digest(plain(raw)),
    )


class TimedMetadata:
    """Opt-in event adapter; only fixed metadata SQL at the original connection.

    Register via capture_queries(..., _observer=metadata). C3 must preregister this
    instrumented mode; it cannot replace a failed/uninstrumented sample afterwards.
    """

    def __init__(self, inventory, *, build_root, log_root, budget):
        self.inventory, self.build_root, self.log_root = inventory, build_root, log_root
        self.records = []
        self.budget = budget

    def before(self, connection, name):
        raw = {"preparation_started_ns": time.monotonic_ns(), "repository_query": name}
        self.records.append(raw)  # preserve even failed preparations
        self.inventory.require_complete()
        if (
            read_build_inventory(self.build_root, self.inventory.build["groups"])
            != self.inventory.build
        ):
            raise ValueError("actual diagnostic build identity differs")
        identity = sync_row(connection, sql.IDENTITY)
        capabilities = sync_row(connection, sql.CAPABILITIES)
        raw.update(identity=plain(identity), capabilities=plain(capabilities))
        sql.validate_capabilities(identity, capabilities, self.inventory)
        raw["before"] = sync_statistics(connection, identity)
        wait, fact = boundary_wait(connection, identity)
        raw["wait_before"] = plain(wait)
        nonce = uuid4().hex
        raw["start_marker"] = f"SELECT 1 /* opencitadel-diagnostic-{nonce}-start */"
        raw["end_marker"] = f"SELECT 1 /* opencitadel-diagnostic-{nonce}-end */"
        mark = LogRange.start(log_path(self.log_root, identity))
        raw["log_range"] = {"device": mark.device, "inode": mark.inode, "offset": mark.offset}
        connection.exec_driver_sql(raw["start_marker"]).close()
        raw["preparation_ended_ns"] = time.monotonic_ns()
        return {"raw": raw, "mark": mark, "waits": [fact]}

    def after(self, connection, statement):
        metadata = statement.metadata
        raw = metadata["raw"]
        identity = raw["identity"]
        raw["post_started_ns"] = time.monotonic_ns()
        connection.exec_driver_sql(raw["end_marker"]).close()
        wait, fact = boundary_wait(connection, identity)
        raw["wait_after"] = plain(wait)
        metadata["waits"].append(fact)
        raw["after"] = sync_statistics(connection, identity)
        raw["post_ended_ns"] = time.monotonic_ns()


def framed_trace(raw, metadata, statement):
    """Require actual emitted nonce anchors, not guessed wall-clock intervals."""
    start = end = None
    lines = raw.splitlines(keepends=True)
    identity = metadata["identity"]
    for index, line in enumerate(lines):
        row = strict_json(line)
        if row.get("pid") != identity["pid"] or row.get("session_id") != identity["session_id"]:
            continue
        message = row.get("message", "")
        if "plan:\n" not in message:
            continue
        document = strict_json(message.split("plan:\n", 1)[1].encode())
        marker = document.get("Query Text")
        if marker in (metadata["start_marker"], metadata["end_marker"]):
            if row.get("txid") != identity["txid"] % 2**32:
                raise ValueError("trace anchor transaction differs")
            if marker == metadata["start_marker"]:
                if start is not None:
                    raise ValueError("duplicate start anchor")
                start = (index, row["line_num"])
            else:
                if end is not None:
                    raise ValueError("duplicate end anchor")
                end = (index, row["line_num"])
    if start is None or end is None or start[0] >= end[0]:
        raise ValueError("missing actual complete trace anchors")
    selected = b"".join(lines[start[0] + 1 : end[0]])
    matching = [
        strict_json(x)
        for x in selected.splitlines()
        if strict_json(x).get("pid") == identity["pid"]
    ]
    if (
        not matching
        or matching[0]["line_num"] != start[1] + 1
        or matching[-1]["line_num"] != end[1] - 1
    ):
        raise ValueError("missing trace adjacent to anchor")
    top, nested = parse_trace(
        selected,
        pid=identity["pid"],
        session_id=identity["session_id"],
        txid=identity["txid"] % 2**32,
        sql=statement.sql,
    )
    binding = TraceBinding(
        backend_pid=identity["pid"],
        backend_start=identity["backend_start"],
        session_id=identity["session_id"],
        transaction_id=identity["txid"],
        start_line=start[1],
        end_line=end[1],
        backend_records=len(matching),
        plan_query_ids=[p.query_id for p in nested] + [top.query_id],
        sql_digest=canonical_digest(statement.sql),
        parameters_digest=canonical_digest(plain(statement.parameters)),
        raw_digest=canonical_digest(raw.decode()),
        start_marker_digest=canonical_digest(metadata["start_marker"]),
        end_marker_digest=canonical_digest(metadata["end_marker"]),
    )
    return top, nested, binding


def capture_digest(capture):
    """C3 retains this from original source; consumer joins it to diagnostics."""
    return canonical_digest(
        [
            {
                "repository_query": s.repository_query,
                "sql_digest": canonical_digest(s.sql),
                "parameters_digest": canonical_digest(plain(s.parameters)),
                "backend_pid": s.backend_pid,
                "started_ns": s.started_ns,
                "ended_ns": s.ended_ns,
            }
            for s in capture.statements
        ]
    )


def replay_statement(s, raw, trace, current, waits, inventory, ordinal, *, budget):
    """Shared original statement predicate, no SQL, file access or callbacks."""
    from scripts.execution_capacity.evidence_owner import copy_original

    raw = copy_original(raw, budget=budget)
    identity = raw["identity"]
    sql.validate_capabilities(identity, raw["capabilities"], inventory)
    if not (
        0
        < raw["preparation_started_ns"]
        <= raw["preparation_ended_ns"]
        <= s.started_ns
        <= s.ended_ns
        <= raw["post_started_ns"]
        <= raw["post_ended_ns"]
    ):
        raise ValueError("original diagnostic capture clock order differs")
    if len(waits) != 2:
        raise ValueError("original diagnostic wait observations absent")
    for observed, name in zip(waits, ("wait_before", "wait_after"), strict=True):
        original = raw[name]
        if (
            original["pid"] != identity["pid"]
            or original["backend_start"] != identity["backend_start"]
            or observed.backend_pid != identity["pid"]
            or observed.query_id != original["query_id"]
            or observed.waiting_for_lock != (original["wait_event_type"] == "Lock")
            or observed.blockers != original["blockers"]
            or observed.ungranted_locks != original["ungranted_locks"]
            or observed.raw_digest != canonical_digest(plain(original))
        ):
            raise ValueError("original diagnostic wait identity differs")
    budget.reserve(len(trace) * 64, rows=len(trace), largest=len(trace))
    data = {"waits": waits}
    errors = []
    before = after = plan = None
    nested = []
    trace_digest = None
    trace_binding = None
    attribution = "aggregate-only"
    try:
        if (
            any(
                current[k] != identity[k]
                for k in (
                    "database_name",
                    "database_system_identifier",
                    "server_version",
                    "migrations",
                )
            )
            or s.backend_pid != identity["pid"]
        ):
            raise ValueError("original timed database/backend identity differs")
        plan, nested, trace_binding = framed_trace(trace, raw, s)
        trace_digest = canonical_digest(trace_binding.model_dump(mode="json"))
        if s.repository_query.startswith("analysis.") and not nested:
            raise ValueError("missing original nested analysis work")
        # Complete original plan is primary authority. Concurrency or an
        # absent pgss entry never becomes an invented per-sample delta.
        if (
            raw["before"]["reset"] != raw["after"]["reset"]
            or raw["before"]["dealloc"] != raw["after"]["dealloc"]
        ):
            raise ValueError("statistics reset/eviction during original sample")
        try:
            before, after = sql.attributed_statistics(
                raw["before"],
                raw["after"],
                plan.query_id,
                database_id=identity["database_id"],
                user_id=identity["user_id"],
            )
            attribution = "single-call-delta"
        except sql.StatisticsAttributionUnavailable:
            raw["statistics_attribution"] = "aggregate-only"
    except Exception as error:  # noqa: BLE001 - retain sanitized failure and permit safe cleanup
        errors.append("original_trace_" + type(error).__name__)
    ended = raw.get("post_ended_ns", s.ended_ns)
    statement = StatementDiagnostic(
        ordinal=ordinal,
        repository_query=s.repository_query,
        sql_digest=canonical_digest(s.sql),
        parameters_digest=canonical_digest(plain(s.parameters)),
        backend_pid=s.backend_pid,
        backend_start=identity["backend_start"],
        database_id=identity["database_id"],
        user_id=identity["user_id"],
        started_ns=raw["preparation_started_ns"],
        ended_ns=ended,
        execution_started_ns=s.started_ns,
        execution_ended_ns=s.ended_ns,
        before=before,
        after=after,
        source_started_ns=s.started_ns,
        source_ended_ns=s.ended_ns,
        plan=plan,
        nested_plans=nested,
        waits=data["waits"],
        lock_wait_ns=sql.wait_quantity(data["waits"], s.started_ns, s.ended_ns),
        instrumentation_ns=unavailable(
            "auto_explain overhead not isolated; all observation remains in original latency"
        ),
        observer_elapsed_ns=(raw["preparation_ended_ns"] - raw["preparation_started_ns"])
        + (ended - raw.get("post_started_ns", ended)),
        raw_digest=canonical_digest(plain(raw)),
        statistics_attribution=attribution,
        statistics_digest=canonical_digest(
            plain({"before": raw.get("before"), "after": raw.get("after")})
        ),
        plan_origin="original-timed-execution",
        trace_binding_digest=trace_digest,
        trace_binding=trace_binding,
        errors=errors,
    )
    return statement, raw


async def collect_original(capture, metadata, observer, request):
    from scripts.execution_capacity.evidence_owner import copy_original

    budget = metadata.budget
    result = DiagnosticResult(
        request,
        canonical_digest(metadata.inventory.database),
        metadata.inventory.build["digest"],
        time.monotonic_ns(),
        buffer_origin="original-timed-execution",
    )
    result.private = {"preparations": [], "traces": [], "capture": None, "current": None}
    try:
        if (
            not capture.closed
            or capture.errors
            or getattr(capture, "diagnosed", False)
            or not capture.statements
            or any(s.metadata is None or s.ended_ns is None for s in capture.statements)
            or (capture.sample_id, capture.action_id, capture.clock_id, capture.clone_id)
            != tuple(
                request.identity[k]
                for k in ("sample_id", "action_id", "observer_clock_id", "clone_id")
            )
        ):
            raise ValueError("incomplete/reused/misbound original timed capture")
        retained = {
            name: getattr(capture, name)
            for name in (
                "sample_id",
                "action_id",
                "clock_id",
                "clone_id",
                "expected",
                "closed",
                "errors",
            )
        }
        retained["statements"] = [
            {
                **{
                    name: getattr(statement, name)
                    for name in (
                        "repository_query",
                        "sql",
                        "parameters",
                        "backend_pid",
                        "started_ns",
                        "ended_ns",
                    )
                },
                "waits": statement.metadata["waits"],
            }
            for statement in capture.statements
        ]
        result.private["capture"] = copy_original(retained, budget=budget)
        capture.diagnosed = True
        result.capture_digest = capture_digest(capture)
        result.started_ns = min(
            s.metadata["raw"]["preparation_started_ns"] for s in capture.statements
        )
        current = dict(await observer.fetchrow(sql.IDENTITY))
        result.private["current"] = copy_original(current, budget=budget)
        for ordinal, statement in enumerate(capture.statements):
            data = statement.metadata
            raw = data["raw"]
            # Reading/parsing the new retained trace uses the same cumulative root.
            trace = data["mark"].finish(log_path(metadata.log_root, current), budget=budget)
            result.private["traces"].append(trace.decode())
            observed, retained_raw = replay_statement(
                statement,
                raw,
                trace,
                current,
                data["waits"],
                metadata.inventory,
                ordinal,
                budget=budget,
            )
            result.private["preparations"].append(retained_raw)
            result.statements.append(observed)
    except Exception as error:  # noqa: BLE001 - retain failed original acquisition
        result.errors.append("original_acquisition_" + type(error).__name__)
    finally:
        result.ended_ns = time.monotonic_ns()
    return result


def replay_original(value, inventory, *, budget):
    """Rebuild only closed diagnostic types from retained original operands."""
    from dataclasses import fields
    from types import SimpleNamespace

    from scripts.execution_capacity.evidence_owner import copy_original
    from scripts.execution_capacity.pg_diagnostics import DiagnosticRequest

    value = copy_original(value, budget=budget)
    if set(value) != {field.name for field in fields(DiagnosticResult)}:
        raise ValueError("complete original diagnostic result required")
    private = value["private"]
    if (
        set(private) != {"preparations", "traces", "capture", "current"}
        or type(private["capture"]) is not dict
    ):
        raise ValueError("original diagnostic capture/current evidence required")
    capture = private["capture"]
    if (
        set(capture)
        != {
            "sample_id",
            "action_id",
            "clock_id",
            "clone_id",
            "expected",
            "closed",
            "errors",
            "statements",
        }
        or capture["closed"] is not True
        or capture["errors"]
        or not capture["statements"]
    ):
        raise ValueError("original diagnostic capture incomplete")
    request = DiagnosticRequest(**value["request"])
    if tuple(capture[k] for k in ("sample_id", "action_id", "clock_id", "clone_id")) != tuple(
        request.identity[k] for k in ("sample_id", "action_id", "observer_clock_id", "clone_id")
    ):
        raise ValueError("original diagnostic capture request differs")
    from app.infrastructure.execution.query_observation import QUERY_NAMES

    names = [row["repository_query"] for row in capture["statements"]]
    if (
        names != capture["expected"]
        or len(names) != len(set(names))
        or not set(names) <= QUERY_NAMES
        or len(names) != len(private["preparations"])
        or len(names) != len(private["traces"])
    ):
        raise ValueError("original diagnostic statement coverage differs")
    statements = []
    originals = []
    for index, row in enumerate(capture["statements"]):
        if (
            set(row)
            != {
                "repository_query",
                "sql",
                "parameters",
                "backend_pid",
                "started_ns",
                "ended_ns",
                "waits",
            }
            or type(row["sql"]) is not str
        ):
            raise ValueError("original diagnostic statement inputs differ")
        statement = SimpleNamespace(**row)
        originals.append(statement)
        trace = private["traces"][index]
        if type(trace) is not str:
            raise ValueError("original diagnostic trace type differs")
        budget.reserve(len(trace) * 4, rows=0)
        observed, raw = replay_statement(
            statement,
            private["preparations"][index],
            trace.encode(),
            private["current"],
            [WaitObservation.model_validate(wait) for wait in row["waits"]],
            inventory,
            index,
            budget=budget,
        )
        if observed.errors or raw != private["preparations"][index]:
            raise ValueError("original diagnostic statement predicate rejected")
        statements.append(observed)
    checks = {
        "errors": not value["errors"],
        "origin": value["buffer_origin"] == "original-timed-execution",
        "database": value["database_digest"] == canonical_digest(inventory.database),
        "build": value["build_digest"] == inventory.build["digest"],
        "capture": value["capture_digest"] == capture_digest(SimpleNamespace(statements=originals)),
        "start": value["started_ns"]
        == min(raw["preparation_started_ns"] for raw in private["preparations"]),
        "end": value["ended_ns"] >= max(raw["post_ended_ns"] for raw in private["preparations"]),
        "statements": value["statements"] == [row.model_dump() for row in statements],
    }
    for name, matches in checks.items():
        if not matches:
            raise ValueError("complete original diagnostic " + name + " differs")
    return DiagnosticResult(**{**value, "request": request, "statements": statements})


def diagnostic_inventory(result, current, base, *, budget):
    """Select only independently verified complete final/base source snapshots."""
    matches = []
    for inventory in (current, base):
        if inventory is None:
            continue
        before = budget.bytes
        budget.charge({"database": inventory.database, "build": inventory.build})
        budget.reserve((budget.bytes - before) * 64, rows=0)
        if (
            result["database_digest"] == canonical_digest(inventory.database)
            and result["build_digest"] == inventory.build["digest"]
        ):
            matches.append(inventory)
    if len(matches) != 1:
        raise ValueError("unique verified diagnostic inventory required")
    return matches[0]
