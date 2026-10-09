"""Cumulative final facts and failure-safe source shutdown, never history repair.

The 32 GiB known-role bound is NOT a complete package bound. These complete raw
rows/counts remain available for mandatory bounded-consumer sizing after C2c.
"""

import time
from dataclasses import dataclass, field, fields
from decimal import Decimal, InvalidOperation

# Fixed table names only. SELECT * retains all original attempts and states;
# application classification never narrows acquisition to the latest/current row.
from scripts.acceptance.capacity_c2c_models import SETTLEMENT_TABLES as TABLES
from scripts.acceptance.capacity_io import canonical_digest
from scripts.execution_capacity.inventory_sql import IDENTITY, plain, read_snapshot


def _zero(value):
    try:
        return not isinstance(value, bool) and Decimal(str(value)) == 0
    except InvalidOperation:
        return False


def _identity(row):
    return str(
        next(
            (
                row[k]
                for k in (
                    "call_identity",
                    "id",
                    "run_id",
                    "aggregate_id",
                    "stream_id",
                    "key",
                    "scope_key",
                )
                if row.get(k) is not None
            ),
            canonical_digest(row),
        )
    )


def classify(rows, inventory):
    inventory.require_complete()
    owners = {str(r["stream_id"]): r["owner_scope_key"] for r in inventory.owners}
    scopes = set(owners.values())
    batches = {str(r["batch_id"]) for r in [*inventory.attempts, *inventory.judges]}
    batches |= {str(r["batch_id"]) for r in getattr(inventory, "versions", ()) if r.get("batch_id")}
    objects = {r["key"] for r in inventory.objects}
    result = []

    def emit(kind, row, state):
        result.append(
            {
                "kind": kind,
                "identity": _identity(row),
                "state": state,
                "digest": canonical_digest(row),
            }
        )

    def owned(row):
        run = next(
            (
                str(row[k])
                for k in ("run_id", "aggregate_id", "stream_id")
                if row.get(k) is not None
            ),
            None,
        )
        scope = row.get("scope_key") or row.get("owner_scope_key")
        if scope is None and row.get("owner_user_id") is not None:
            scope = "user:" + str(row["owner_user_id"])
        elif scope is None and row.get("team_id") is not None:
            scope = "team:" + str(row["team_id"])
        return (
            (run is None or run in owners)
            and (scope is None or scope in scopes)
            and (run is None or scope is None or owners.get(run) == scope)
        )

    dispatches = {
        (r["scope_key"], str(r["call_identity"])): r for r in rows["execution_model_dispatches"]
    }
    reservations = {
        (r["scope_key"], str(r["call_identity"])): r for r in rows["evaluation_budget_reservations"]
    }
    settlements = {
        (r["scope_key"], str(r["call_identity"])): r for r in rows["execution_model_settlements"]
    }
    for key in sorted(set(dispatches) | set(reservations) | set(settlements)):
        dispatch, reservation, settlement = (
            dispatches.get(key),
            reservations.get(key),
            settlements.get(key),
        )
        row = dispatch or reservation or settlement
        state = (
            "error"
            if dispatch is None or not owned(dispatch)
            else "settled"
            if reservation is not None
            and reservation["state"] == "settled"
            and reservation.get("settlement") is not None
            and settlement is not None
            and settlement.get("fact") is not None
            else "pending"
        )
        emit("physical-dispatch", row, state)
    skip = {
        "execution_model_dispatches",
        "evaluation_budget_reservations",
        "execution_model_settlements",
    }
    seen_batches = set()
    for table in TABLES:
        if table in skip:
            continue
        for row in rows[table]:
            state = "retained"
            if not owned(row):
                state = "error"
            elif table == "execution_activity_tasks":
                if (
                    row.get("status") not in {"succeeded", "failed", "cancelled"}
                    or row.get("claim_owner") is not None
                    or row.get("claim_deadline") is not None
                ):
                    state = "pending"
            elif table == "execution_command_inbox":
                if (
                    row.get("status") not in {"accepted", "rejected"}
                    or row.get("claim_deadline") is not None
                ):
                    state = "pending"
            elif table == "execution_scheduled_commands":
                envelope = row.get("command_envelope") or {}
                if str(envelope.get("stream_id")) not in owners:
                    state = "error"
                elif (
                    row.get("status") not in {"cancelled", "fired"}
                    or row.get("claim_deadline") is not None
                ):
                    state = "pending"
                elif row.get("status") == "fired":  # noqa: SIM102 - keep retained timer rationale adjacent
                    # A fired timer is retained only with its actual consumed command.
                    if not any(
                        str(i.get("command_id")) == str(envelope.get("command_id"))
                        and i.get("status") in {"accepted", "rejected"}
                        for i in rows["execution_command_inbox"]
                    ):
                        state = "pending"
            elif table == "execution_run_projection":
                if row.get("terminal") is not True or row.get("decision_due_at") is not None:
                    state = "pending"
            elif table == "evaluation_budget_buckets":
                if any(
                    not _zero(row.get(k)) for k in ("slots", "reserved_tokens", "reserved_money")
                ):
                    state = "pending"
            elif table == "evaluation_execution_pools":
                if row.get("occupied") != 0:
                    state = "pending"
            elif table == "evaluation_execution_leases":
                # Released capacity does not settle a physical non-idempotent
                # effect. Match subject scheduler and judge/archive predicates
                # for every historical row, independently of newer attempts.
                persisted = row.get("state") or {}
                unknown = (
                    persisted.get("failure_code") == "NON_IDEMPOTENT_OUTCOME_UNKNOWN"
                    or any(item[1] == "unknown" for item in persisted.get("settled_activities", []))
                    or any(
                        item[2] == "NON_IDEMPOTENT_OUTCOME_UNKNOWN"
                        for item in persisted.get("activity_failure_codes", [])
                    )
                )
                if row.get("phase") != "released" or unknown:
                    state = "pending"
            elif table == "evaluation_batches":
                identity = str(row["id"])
                seen_batches.add(identity)
                if identity not in batches:
                    state = "error"
                elif (
                    row.get("status")
                    not in {"completed", "completed_with_errors", "failed", "cancelled", "rejected"}
                    or row.get("cleanup_status") != "clean"
                ):
                    state = "pending"
            elif table == "evaluation_environment_leases":
                if str(row.get("case_slot", {}).get("batch_id")) not in batches:
                    state = "error"
                elif row.get("state") != "verified_clean":
                    state = "pending"
            elif table == "evaluation_environment_operations":
                if (
                    row.get("status") != "done"
                    or row.get("error") is not None
                    or row.get("claim_until") is not None
                    or not row.get("receipt")
                ):
                    state = "pending"
            elif table == "evaluation_object_intents":
                if row.get("cleaned_at") is None and row.get("storage_key") not in objects:
                    state = "pending"
            elif table == "execution_recovery_requests":
                if row.get("status") != "completed":
                    state = "pending"
            elif table == "execution_view_generations":
                if row.get("status") in {"building", "failed"}:
                    state = "pending"
            elif table == "artifact_production_receipts":
                if row.get("event_id") is None:
                    state = "pending"
            elif table in {"artifact_upload_intents", "artifact_retired_objects"}:
                if row.get("cleaned_at") is None:
                    state = "pending"
            elif table == "scheduled_jobs":
                if row.get("enabled") is not False:
                    state = "pending"
            else:
                # These unsupported extra side-effect families are not silently
                # certified from an arbitrary status label. Their rows survive.
                state = "error"
            emit(table, row, state)
    for missing in sorted(batches - seen_batches):
        emit("evaluation_batches", {"id": missing}, "error")
    for row in rows.get("execution_outbox", ()):
        emit(
            "execution_outbox",
            row,
            "retained"
            if owned(row)
            and row.get("delivered_at") is not None
            and row.get("claim_deadline") is None
            else "pending",
        )
    return result


class SettlementReader:
    def __init__(self, sessions, authorization, inventory):
        self.sessions, self.authorization, self.inventory = sessions, authorization, inventory

    async def read(self):
        result = {"rows": {}, "reads": [], "issues": [], "complete": False}
        try:
            async with read_snapshot(self.sessions, self.authorization) as query:
                await collect_settlement(query, self.inventory, result)
        except Exception as error:  # noqa: BLE001 - retain failed acquisition and continue safe cleanup
            result["issues"].append(
                {"kind": "database-read", "state": "error", "type": type(error).__name__}
            )
        if "query" in locals():
            result["reads"] = query.finish_outputs()
        result["counts"] = {k: len(v) for k, v in result["rows"].items()}
        return result


async def collect_settlement(query, inventory, result):
    result["database"] = plain(await query.rows("database", IDENTITY))
    actual = result["database"][0]
    if (
        any(
            actual[k] != inventory.database[k]
            for k in ("database_name", "database_system_identifier", "migrations")
        )
        or actual["read_only"] != "on"
        or actual["isolation"] != "repeatable read"
    ):
        raise ValueError("final readonly database identity differs")
    for table in TABLES:
        result["rows"][table] = plain(await query.rows(table, "SELECT * FROM " + table))
    result["rows"]["execution_outbox"] = plain(
        await query.rows(
            "execution_outbox",
            "SELECT o.*,e.stream_id FROM execution_outbox o JOIN execution_events e ON e.position=o.event_position",
        )
    )
    result["reads"] = query.finish_outputs()
    result["dispositions"] = classify(result["rows"], inventory)
    result["issues"] = [r for r in result["dispositions"] if r["state"] in {"pending", "error"}]
    result["complete"] = True


@dataclass
class Quiescence:
    started_ns: int = field(default_factory=time.monotonic_ns)
    ended_ns: int = 0
    phases: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    writers: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)
    final: object = None
    complete: bool = False

    def require_complete(self):
        if not self.complete or self.errors:
            raise ValueError("quiescence unresolved; seal and resource reuse forbidden")
        return self


async def quiesce(*, writers, workloads, diagnostics, uploads, final):
    """Drain ordinary services, retain diagnostics errors, always attempt shutdown.

    Real implementations below consume actual resources. Boundary doubles in unit
    tests are not public proof interfaces; C2c4 consumes the retained result only
    from this coordinator with independently verified ownership.
    """
    result = Quiescence()

    async def step(name, action):
        before = time.monotonic_ns()
        try:
            return await action()
        except BaseException as error:  # noqa: BLE001 - cancellation still requires safe cleanup
            result.errors.append({"stage": name, "type": type(error).__name__})
            return None
        finally:
            result.phases.append({"stage": name, "start_ns": before, "end_ns": time.monotonic_ns()})

    try:
        await step("admissions", writers.stop_admissions)
        for workload in workloads:
            await step("settlement", workload.finish)
        for job in diagnostics:
            value = await step("diagnostics", job.collect)
            if value is not None:
                result.diagnostics.append(value)
                if (
                    getattr(value, "errors", None)
                    or not getattr(value, "statements", ())
                    or any(s.errors for s in value.statements)
                ):
                    result.errors.append({"stage": "diagnostics", "type": "InvalidEvidence"})
    finally:
        for upload in uploads:
            await step("uploads", upload.drain)
        result.writers = await step("writers", writers.stop) or []
        if not result.writers or any(r.get("exited") is not True for r in result.writers):
            result.errors.append({"stage": "writer-exit", "type": "UnverifiedExit"})
        else:
            result.final = await step("final-inventory", final.read)
            if result.final is None or not result.final["complete"] or result.final["issues"]:
                result.errors.append({"stage": "final-inventory", "type": "UnresolvedFacts"})
        result.ended_ns = time.monotonic_ns()
    result.complete = not result.errors
    if result.complete:
        try:
            validate_quiescence(result)
        except (ValueError, TypeError, KeyError):
            result.errors.append({"stage": "quiescence", "type": "InvalidEvidence"})
            result.complete = False
    return result


@dataclass
class OriginalDiagnostics:
    capture: object
    metadata: object
    observer: object
    request: object

    async def collect(self):
        from scripts.execution_capacity.pg_diagnostics_timed import collect_original

        return await collect_original(self.capture, self.metadata, self.observer, self.request)


def validate_quiescence(value):
    """Shared completion and actual sequential phase coverage, no flag authority."""
    raw = vars(value) if type(value) is Quiescence else value
    if set(raw) != {item.name for item in fields(Quiescence)}:
        raise ValueError("complete original quiescence fields required")
    if raw["errors"] or raw["complete"] is not True or not 0 < raw["started_ns"] <= raw["ended_ns"]:
        raise ValueError("original quiescence incomplete")
    phases = raw["phases"]
    names = [row["stage"] for row in phases]
    rank = {
        "admissions": 0,
        "settlement": 1,
        "diagnostics": 2,
        "uploads": 3,
        "writers": 4,
        "final-inventory": 5,
    }
    if (
        any(name not in rank for name in names)
        or any(names.count(name) != 1 for name in ("admissions", "writers", "final-inventory"))
        or names != sorted(names, key=rank.__getitem__)
        or names.count("diagnostics") != len(raw["diagnostics"])
    ):
        raise ValueError("original quiescence phase coverage differs")
    last = raw["started_ns"]
    for phase in phases:
        if (
            set(phase) != {"stage", "start_ns", "end_ns"}
            or not last <= phase["start_ns"] <= phase["end_ns"] <= raw["ended_ns"]
        ):
            raise ValueError("original quiescence phase time differs")
        last = phase["end_ns"]
    final = raw["final"]
    phase = phases[-1]
    if (
        not final
        or final["complete"] is not True
        or final["issues"]
        or not phase["start_ns"] <= final["start_ns"] <= final["end_ns"] <= phase["end_ns"]
    ):
        raise ValueError("original final phase observation differs")
    if not raw["writers"] or any(
        row.get("exited") is not True
        or not raw["started_ns"] <= row["observed_ns"] <= phases[-2]["end_ns"]
        for row in raw["writers"]
    ):
        raise ValueError("original quiescence writer result differs")
    for result, phase in zip(
        raw["diagnostics"], [row for row in phases if row["stage"] == "diagnostics"], strict=True
    ):
        diagnostic = vars(result) if hasattr(result, "__dataclass_fields__") else result
        if (
            diagnostic["errors"]
            or not diagnostic["statements"]
            or any(
                (statement.errors if hasattr(statement, "errors") else statement["errors"])
                for statement in diagnostic["statements"]
            )
            or not phase["start_ns"] <= diagnostic["ended_ns"] <= phase["end_ns"]
        ):
            raise ValueError("original diagnostic completion differs")
