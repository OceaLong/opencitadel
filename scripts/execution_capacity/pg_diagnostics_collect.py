"""Actual fixed-statement diagnostic acquisition, after host timing completed.

Only privately captured repository executions are accepted. The original live
SQLAlchemy connection retains principal authorization and bound driver values.
One replay runs in a rolled-back savepoint; PG's preloaded auto_explain produces
actual JSON ANALYZE/BUFFERS, including nested signed-function statements. This
is a diagnostic execution, not timed-cold buffer evidence. No adaptive retries.
"""

import asyncio
import contextlib
import json
import time
from pathlib import Path

from scripts.acceptance.capacity_diagnostics import StatementDiagnostic, WaitObservation
from scripts.acceptance.capacity_io import canonical_digest
from scripts.execution_capacity import pg_diagnostics_sql as sql
from scripts.execution_capacity.inventory import read_build_inventory
from scripts.execution_capacity.pg_diagnostics import DiagnosticResult
from scripts.execution_capacity.pg_diagnostics_log import LogRange, parse_trace
from scripts.execution_capacity.pg_diagnostics_plan import unavailable
from sqlalchemy import text

from app.infrastructure.execution.query_observation import QueryCapture


def plain(value):
    return json.loads(json.dumps(value, default=str, sort_keys=True, allow_nan=False))


async def row(connection, statement):
    return dict((await connection.execute(text(statement))).mappings().one())


def log_path(root, identity):
    root = Path(root).resolve()
    path = root / identity["logfile"]
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("diagnostic log outside verified private root")
    return path


async def observe_wait(observer, identity, observations, raw):
    started = time.monotonic_ns()
    value = await observer.fetchrow(sql.WAIT, identity["pid"])
    ended = time.monotonic_ns()
    if (
        not value
        or value["pid"] != identity["pid"]
        or value["backend_start"] != identity["backend_start"]
    ):
        raise ValueError("missing/reused diagnostic backend wait observation")
    value = plain(dict(value))
    raw.append({"started_ns": started, "ended_ns": ended, "value": value})
    observations.append(
        WaitObservation(
            started_ns=started,
            ended_ns=ended,
            backend_pid=identity["pid"],
            query_id=value["query_id"],
            waiting_for_lock=value["wait_event_type"] == "Lock",
            blockers=value["blockers"],
            ungranted_locks=value["ungranted_locks"],
            raw_digest=canonical_digest(value),
        )
    )


async def sample_waits(observer, identity, observations, raw, stopped):
    for _ in range(1501):
        await observe_wait(observer, identity, observations, raw)
        if stopped.is_set():
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stopped.wait(), timeout=0.02)
    raise ValueError("fixed diagnostic wait-sampling bound exhausted")


async def collect_statement(statement, connection, observer, identity, ordinal, root):
    started = time.monotonic_ns()
    raw = {"waits": []}
    values = {
        "ordinal": ordinal,
        "repository_query": statement.repository_query,
        "sql_digest": canonical_digest(statement.sql),
        "parameters_digest": canonical_digest(plain(statement.parameters)),
        "backend_pid": identity["pid"],
        "backend_start": identity["backend_start"],
        "database_id": identity["database_id"],
        "user_id": identity["user_id"],
        "started_ns": started,
        "before": None,
        "after": None,
        "plan": None,
        "nested_plans": [],
        "waits": [],
        "source_started_ns": statement.started_ns,
        "source_ended_ns": statement.ended_ns,
        "lock_wait_ns": unavailable("no completed bounded sampling interval"),
        "instrumentation_ns": unavailable(
            "no independent observer-overhead calibration; elapsed observer work retained"
        ),
        "errors": [],
    }
    transaction, sampler = None, None
    stopped = asyncio.Event()
    try:
        transaction = await connection.begin_nested()
        raw["before"] = await sql.statistics(observer, identity["database_id"], identity["user_id"])
        mark = LogRange.start(log_path(root, identity))
        raw["log_range"] = {"device": mark.device, "inode": mark.inode, "offset": mark.offset}
        await observe_wait(observer, identity, values["waits"], raw["waits"])
        execution_start = time.monotonic_ns()
        sampler = asyncio.create_task(
            sample_waits(observer, identity, values["waits"], raw["waits"], stopped)
        )
        try:
            async with asyncio.timeout(30):
                result = await connection.exec_driver_sql(statement.sql, statement.parameters)
                result.close()
        finally:
            execution_end = time.monotonic_ns()
            stopped.set()
            await sampler
            sampler = None
        raw["execution_interval"] = [execution_start, execution_end]
        values.update(execution_started_ns=execution_start, execution_ended_ns=execution_end)
        values["lock_wait_ns"] = sql.wait_quantity(values["waits"], execution_start, execution_end)
        raw["after"] = await sql.statistics(observer, identity["database_id"], identity["user_id"])
        # Observer reads the current logging filename without emitting an extra
        # target-backend plan into the captured append interval.
        current_log = await observer.fetchval("SELECT pg_current_logfile('jsonlog')")
        trace = mark.finish(log_path(root, {"logfile": current_log}))
        raw["log"] = trace.decode("utf-8")
        values["plan"], values["nested_plans"] = parse_trace(
            trace,
            pid=identity["pid"],
            session_id=identity["session_id"],
            txid=identity["txid"] % 2**32,
            sql=statement.sql,
        )
        values["before"], values["after"] = sql.attributed_statistics(
            raw["before"],
            raw["after"],
            values["plan"].query_id,
            database_id=identity["database_id"],
            user_id=identity["user_id"],
        )
        if statement.repository_query.startswith("analysis.") and not values["nested_plans"]:
            raise ValueError("signed wrapper lacks actual nested execution plans")
    except Exception as error:  # noqa: BLE001 - retain sanitized failure and permit safe cleanup
        values["errors"].append("diagnostic_statement_" + type(error).__name__)
        raw["error_type"] = type(error).__name__  # driver exception text can contain signed values
    finally:
        stopped.set()
        if sampler is not None:
            try:
                await sampler
            except Exception as error:  # noqa: BLE001 - retain sanitized failure and permit safe cleanup
                values["errors"].append("wait_observer_" + type(error).__name__)
        if transaction is not None:
            try:
                await transaction.rollback()
            except Exception as error:  # noqa: BLE001 - retain sanitized failure and permit safe cleanup
                values["errors"].append("diagnostic_rollback_" + type(error).__name__)
        ended = time.monotonic_ns()
    values.update(
        ended_ns=ended, observer_elapsed_ns=ended - started, raw_digest=canonical_digest(plain(raw))
    )
    return StatementDiagnostic(**values), raw


async def collect(capture, connection, observer, inventory, request, *, build_root, log_root):
    """Consume one actual capture once; return failures without closing services.

    C3 supplies an authorize_request result from the real host transport ledger and
    runs this only on its bound guest. C2c3 still owns all subsequent safe cleanup.
    Same retained connection is mandatory; a pool reacquisition cannot establish the
    same signed authorization, transaction, backend or query parameter identity.
    """
    result = DiagnosticResult(
        request,
        canonical_digest(inventory.database),
        inventory.build.get("digest", "0" * 64),
        time.monotonic_ns(),
    )
    try:
        if (
            not isinstance(capture, QueryCapture)
            or not capture.closed
            or capture.errors
            or getattr(capture, "diagnosed", False)
            or not 1 <= len(capture.statements) <= 32
            or any(s.repository_query.startswith("analysis.") for s in capture.statements)
            or (capture.sample_id, capture.action_id, capture.clock_id, capture.clone_id)
            != tuple(
                request.identity[k]
                for k in ("sample_id", "action_id", "observer_clock_id", "clone_id")
            )
            or any(
                s.connection is not connection.sync_connection or s.ended_ns is None
                for s in capture.statements
            )
        ):
            raise ValueError("incomplete/reused/wrong-connection repository capture")
        capture.diagnosed = True
        result.capture_digest = canonical_digest(
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
        inventory.require_complete()
        actual_build = read_build_inventory(build_root, inventory.build["groups"])
        if actual_build != inventory.build:
            raise ValueError("actual diagnostic build identity changed")
        identity = await row(connection, sql.IDENTITY)
        capabilities = await row(connection, sql.CAPABILITIES)
        result.private.update(
            identity=plain(identity), capabilities=plain(capabilities), statements=[]
        )
        sql.validate_capabilities(identity, capabilities, inventory)
        observed_identity = dict(await observer.fetchrow(sql.IDENTITY))
        if (
            observed_identity["pid"] == identity["pid"]
            or any(
                observed_identity[k] != identity[k]
                for k in (
                    "database_name",
                    "database_system_identifier",
                    "server_version",
                    "migrations",
                )
            )
            or any(s.backend_pid != identity["pid"] for s in capture.statements)
        ):
            raise ValueError("observer/target database or backend identity differs")
        for ordinal, statement in enumerate(capture.statements):
            facts, raw = await collect_statement(
                statement, connection, observer, identity, ordinal, log_root
            )
            result.statements.append(facts)
            result.private["statements"].append(raw)
            # A failed rollback makes every further target use unsafe. There is
            # no retry or replacement statement; cleanup retains the failure.
            if any(e.startswith("diagnostic_rollback_") for e in facts.errors):
                break
    except Exception as error:  # noqa: BLE001 - retain sanitized failure and permit safe cleanup
        result.errors.append("diagnostic_acquisition_" + type(error).__name__)
    finally:
        result.ended_ns = time.monotonic_ns()
    return result
