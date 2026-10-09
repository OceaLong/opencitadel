"""Fail-closed verification of expected timeout retention, never budget settlement.

These complete private source rows are compared across the rejected public
archive command. A verified receipt closes local housekeeping, while unknown
external effects and their conservative holds remain an explicit open obligation.
"""

import json
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

TABLES = frozenset(
    {
        "batches",
        "results",
        "attempts",
        "intents",
        "judge_work",
        "invalidations",
        "bindings",
        "namespaces",
        "reservations",
        "dispatches",
        "settlements",
        "execution_leases",
        "environment_leases",
        "score_sets",
        "scores",
        "batch_events",
        "events",
        "audits",
        "tasks",
        "projections",
        "reviews",
        "archives",
        "buckets",
    }
)
REQUIRED = TABLES - {"settlements", "audits", "tasks", "reviews", "archives", "environment_leases"}

SAFE_REASONS = (
    frozenset(
        [
            "active execution lease",
            "active local send",
            "active or missing Judge work",
            "active or missing run projection",
            "active review",
            "authoritative environment mode missing",
            "batch already archived",
            "complete dispatch budget required",
            "complete exact run bindings required",
            "complete formal audit history required",
            "complete owned purpose ledger required",
            "complete source ledger/scoring/audit changed across rejected archive",
            "complete typed source tables required",
            "environment cleanup incomplete",
            "exact batch required",
            "expected terminal clean timeout batch required",
            "expected unknown reservation missing",
            "foreign Judge intent",
            "foreign attempt",
            "foreign audit",
            "foreign batch audit",
            "foreign demand",
            "foreign dispatch",
            "foreign formal event",
            "foreign owned scope",
            "foreign run owner",
            "foreign score",
            "foreign scoring history",
            "foreign settlement",
            "formal audit hashes missing",
            "formal audit stream incomplete",
            "incomplete invalidation history",
            "incomplete namespace ledger",
            "invalid amount",
            "invalid typed rows",
            "isolated environment proof missing",
            "original null Judge error history missing",
            "read-only source required",
            "recorded environment authority inconsistent",
            "subject effect is not clean",
            "scheduler check clock invalid",
            "unexpected unsettled subject call",
            "unknown conservative budget hold released",
            "unknown invalidation missing",
            "unknown namespace closed",
            "unknown namespace state invalid",
            "unknown purpose hold missing",
            "unknown reservation changed or settled",
            "unknown unsafe lease missing",
            "unknown local task proof missing",
            "wrong run purpose",
        ]
    )
    | {"missing " + name for name in TABLES}
    | {"foreign " + name for name in TABLES}
)


def require(condition, reason):
    if not condition:
        raise ValueError("protected retention: " + reason)


def amount(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("protected retention: invalid amount") from error
    require(result.is_finite() and result >= 0, "invalid amount")
    return result


def unsafe_lease(row):
    state = row.get("state") or {}
    return (
        state.get("failure_code") == "NON_IDEMPOTENT_OUTCOME_UNKNOWN"
        or any(
            len(item) > 1 and item[1] == "unknown" for item in state.get("settled_activities", [])
        )
        or any(
            len(item) > 2 and item[2] == "NON_IDEMPOTENT_OUTCOME_UNKNOWN"
            for item in state.get("activity_failure_codes", [])
        )
    )


def validate_snapshot(value, *, batch_id, owner_id):
    UUID(batch_id)
    scope = "user:" + owner_id
    require(
        value.get("schema_version") == 1 and value.get("read_only") is True,
        "read-only source required",
    )
    require(
        (value.get("batch_id"), value.get("owner_id"), value.get("batch_scope"))
        == (batch_id, owner_id, scope),
        "foreign owned scope",
    )
    tables = value.get("tables")
    require(
        isinstance(tables, dict) and set(tables) == TABLES, "complete typed source tables required"
    )
    for name, rows in tables.items():
        require(
            isinstance(rows, list) and all(isinstance(row, dict) for row in rows),
            "invalid typed rows",
        )
        require(name not in REQUIRED or bool(rows), "missing " + name)
        for row in rows:
            if name in {"tasks", "projections"}:
                require(
                    row.get("owner_user_id") == owner_id and row.get("team_id") is None,
                    "foreign run owner",
                )
            elif name == "events":
                require(row.get("owner_scope_key") == scope, "foreign formal event")
            elif name == "audits":
                require(
                    row.get("actor_user_id") == owner_id and row.get("team_id") is None,
                    "foreign audit",
                )
            elif name != "buckets":
                require(row.get("scope_key") == scope, "foreign " + name)
    require(len(tables["batches"]) == 1, "exact batch required")
    batch = tables["batches"][0]
    require(
        batch.get("id") == batch_id
        and batch.get("status") == "completed_with_errors"
        and batch.get("cleanup_status") == "clean",
        "expected terminal clean timeout batch required",
    )
    require(not tables["archives"], "batch already archived")
    results = {row["id"] for row in tables["results"]}
    require(
        all(
            row.get("batch_id") == batch_id
            and row.get("unknown_effect") is False
            and row.get("recovery_pending") is False
            for row in tables["results"]
        ),
        "subject effect is not clean",
    )
    require(all(row.get("result_id") in results for row in tables["attempts"]), "foreign attempt")
    intents = {row["id"]: row for row in tables["intents"]}
    require(
        all(
            row.get("batch_id") == batch_id and row.get("result_id") in results
            for row in intents.values()
        ),
        "foreign Judge intent",
    )
    judge_runs = {row["run_id"] for row in intents.values()}
    run_ids = judge_runs | {row["run_id"] for row in tables["attempts"]}
    require(
        {row["run_id"] for row in tables["bindings"]} == run_ids,
        "complete exact run bindings required",
    )
    require(
        all(
            row.get("purpose")
            == ("evaluation_judge" if row["run_id"] in judge_runs else "evaluation_subject")
            for row in tables["bindings"]
        ),
        "wrong run purpose",
    )
    require(
        {row["intent_id"] for row in tables["judge_work"]} == set(intents)
        and all(row.get("status") in {"settled", "stopped"} for row in tables["judge_work"]),
        "active or missing Judge work",
    )
    require(
        all(
            row.get("status") in {"completed", "failed", "cancelled"}
            or (row.get("kind") == "human" and row.get("status") == "accepted")
            for row in tables["reviews"]
        ),
        "active review",
    )
    projections = tables["projections"]
    require(
        {row["run_id"] for row in projections} == run_ids
        and all(
            row.get("terminal") is True and row.get("active_activity_count") == 0
            for row in projections
        ),
        "active or missing run projection",
    )
    require(
        all(
            row.get("run_id") in run_ids
            and row.get("status")
            in {"succeeded", "failed", "unknown", "cancelled", "dead_lettered"}
            for row in tables["tasks"]
        ),
        "active local send",
    )
    namespaces = {row["id"]: row for row in tables["namespaces"]}
    require(
        set(namespaces) == {batch_id} | {row["namespace_id"] for row in intents.values()}
        and all(row.get("namespace_id") in namespaces for row in tables["bindings"]),
        "incomplete namespace ledger",
    )
    authority = namespaces[batch_id].get("body", {})
    require(
        isinstance(authority, dict)
        and authority.get("mode") in {"recorded", "isolated"}
        and "environment_version" in authority,
        "authoritative environment mode missing",
    )
    if authority["mode"] == "recorded":
        require(
            authority["environment_version"] is None and not tables["environment_leases"],
            "recorded environment authority inconsistent",
        )
    else:
        require(
            isinstance(authority["environment_version"], str)
            and bool(tables["environment_leases"]),
            "isolated environment proof missing",
        )
        UUID(authority["environment_version"])
        require(
            all(
                row.get("state") == "verified_clean"
                and row.get("case_slot", {}).get("batch_id") == batch_id
                for row in tables["environment_leases"]
            ),
            "environment cleanup incomplete",
        )
    dispatches = {row["call_identity"]: row for row in tables["dispatches"]}
    require(all(row.get("run_id") in run_ids for row in dispatches.values()), "foreign dispatch")
    settlements = {row["call_identity"]: row for row in tables["settlements"]}
    require(set(settlements) <= set(dispatches), "foreign settlement")
    reservations = {row["call_identity"]: row for row in tables["reservations"]}
    require(set(reservations) == set(dispatches), "complete dispatch budget required")
    # A physical timeout can leave the durable pre-send reservation dispatching:
    # no after-send fact reached settlement. It remains conservative only with
    # the same independent unsafe execution and retained-ledger proof below.
    unknown = {
        key: row
        for key, row in reservations.items()
        if row.get("state") in {"unknown", "dispatching"}
    }
    require(bool(unknown), "expected unknown reservation missing")
    for key, row in reservations.items():
        demand = row.get("demand", {})
        require(
            demand.get("scope") == scope
            and demand.get("requester") == owner_id
            and demand.get("batch_id") == batch_id
            and demand.get("purpose") in {"evaluation_subject", "evaluation_judge"},
            "foreign demand",
        )
        if key in unknown:
            require(
                dispatches[key]["run_id"] in judge_runs
                and demand.get("purpose") == "evaluation_judge"
                and row.get("settlement") is None
                and row.get("settled_at") is None
                and key not in settlements
                and amount(demand.get("tokens")) > 0,
                "unknown reservation changed or settled",
            )
        else:
            require(
                row.get("state") == "settled"
                and key in settlements
                and row.get("settlement") is not None,
                "unexpected unsettled subject call",
            )
    leases = {row["run_id"]: row for row in tables["execution_leases"]}
    require(
        set(leases) == run_ids and all(row.get("phase") == "released" for row in leases.values()),
        "active execution lease",
    )
    unknown_runs = {dispatches[key]["run_id"] for key in unknown}
    require(all(unsafe_lease(leases[rid]) for rid in unknown_runs), "unknown unsafe lease missing")
    require(
        all(
            any(
                task.get("run_id") == dispatches[key]["run_id"]
                and isinstance(dispatches[key].get("activity_id"), str)
                and task.get("activity_id") == dispatches[key]["activity_id"]
                and task.get("status") == "unknown"
                and task.get("failure_code") == "NON_IDEMPOTENT_OUTCOME_UNKNOWN"
                for task in tables["tasks"]
            )
            for key in unknown
        ),
        "unknown local task proof missing",
    )
    # Namespace close ends admission; it does not settle or release held calls.
    # Complete typed tables still require its state to remain unchanged across
    # the rejected public archive command.
    require(
        all(row.get("state") in {"open", "closed"} for row in namespaces.values()),
        "unknown namespace state invalid",
    )
    sets = {row["id"]: row for row in tables["score_sets"]}
    require(
        all(
            row.get("batch_id") == batch_id and row.get("result_id") in results
            for row in sets.values()
        ),
        "foreign scoring history",
    )
    require(all(row.get("set_id") in sets for row in tables["scores"]), "foreign score")
    failed_sets = {
        row["set_id"]
        for row in tables["scores"]
        if row.get("status") == "error"
        and row.get("value") is None
        and row.get("reason") == "judge_execution_failed"
        and sets[row["set_id"]].get("source") == "model"
    }
    require(bool(failed_sets), "original null Judge error history missing")
    invalidated = set()
    for row in tables["invalidations"]:
        intent = intents.get(row.get("intent_id"), {})
        # Unsafe invalidation can be recorded before the consumer persists its
        # original null error source. A null link then needs that exact intent's
        # later failed model source, rather than an unrelated error in the batch.
        early_error_source = row.get("source_set_id") is None and any(
            source_id in failed_sets
            and source.get("result_id") == intent.get("result_id")
            and source.get("request_id") == "judge:" + str(intent.get("id"))
            and source.get("status") == "failed"
            and type(row.get("evaluation_revision")) is int
            and type(source.get("evaluation_revision")) is int
            and source["evaluation_revision"] > row["evaluation_revision"]
            for source_id, source in sets.items()
        )
        require(
            row.get("intent_id") in intents
            and row.get("batch_id") == batch_id
            and row.get("run_id") == intents[row["intent_id"]]["run_id"]
            and (row.get("source_set_id") in failed_sets or early_error_source),
            "incomplete invalidation history",
        )
        invalidated.add(row["run_id"])
    require(unknown_runs <= invalidated, "unknown invalidation missing")
    require(
        all(row.get("batch_id") == batch_id for row in tables["batch_events"]),
        "foreign batch audit",
    )
    require(
        {row.get("stream_id") for row in tables["events"]} == run_ids,
        "complete formal audit history required",
    )
    for rid in run_ids:
        events = [row for row in tables["events"] if row["stream_id"] == rid]
        require(
            [row.get("stream_version") for row in events] == list(range(1, len(events) + 1)),
            "formal audit stream incomplete",
        )
        require(
            all(
                isinstance(row.get("event_hash"), str) and len(row["event_hash"]) == 64
                for row in events
            ),
            "formal audit hashes missing",
        )
    buckets = {row["key"]: row for row in tables["buckets"]}
    demanded = {
        bucket["key"]
        for row in reservations.values()
        for bucket in row["demand"].get("buckets", [])
        if bucket["key"].startswith(("5:batch:", "6:purpose:"))
    }
    require(set(buckets) == demanded, "complete owned purpose ledger required")
    required_holds = {}
    for row in unknown.values():
        purpose = [
            bucket["key"]
            for bucket in row["demand"].get("buckets", [])
            if bucket["key"].startswith(("5:batch:", "6:purpose:"))
        ]
        require(bool(purpose), "unknown purpose hold missing")
        for name in purpose:
            tokens, money = required_holds.get(name, (Decimal(0), Decimal(0)))
            required_holds[name] = (
                tokens + amount(row["demand"]["tokens"]),
                money + amount(row["demand"].get("money")),
            )
    for name, (tokens, money) in required_holds.items():
        require(
            amount(buckets[name].get("reserved_tokens")) >= tokens
            and amount(buckets[name].get("reserved_money")) >= money,
            "unknown conservative budget hold released",
        )
    return {
        "resource_id": batch_id,
        "batch_scope": scope,
        "owner_id": owner_id,
        "physically_deleted": False,
        "archived": False,
        "settled": False,
        "future_obligation": "open",
        "immutable_unknown_obligations": True,
        "local_active_sends": 0,
        "environment_status": "clean",
    }


COMPARISON_POLICY = "typed-source-equality-with-monotonic-judge-work-checked-at"


def scheduler_clock_evidence(before, after):
    first, second = before["tables"]["judge_work"], after["tables"]["judge_work"]
    require(len(first) == len(second), "scheduler check clock invalid")
    observations = []
    for left, right in zip(first, second, strict=True):
        require(
            (left.get("intent_id"), left.get("scope_key"))
            == (right.get("intent_id"), right.get("scope_key"))
            and ("checked_at" in left) == ("checked_at" in right),
            "scheduler check clock invalid",
        )
        if "checked_at" not in left:
            continue
        old, new = left["checked_at"], right["checked_at"]
        if old is None and new is None:
            continue
        require(isinstance(old, str) and isinstance(new, str), "scheduler check clock invalid")
        try:
            earlier, later = datetime.fromisoformat(old), datetime.fromisoformat(new)
        except ValueError as error:
            raise ValueError("protected retention: scheduler check clock invalid") from error
        require(
            earlier.utcoffset() is not None and later.utcoffset() is not None and later >= earlier,
            "scheduler check clock invalid",
        )
        observations.append(
            {
                "table": "judge_work",
                "field": "checked_at",
                "intent_id": left["intent_id"],
                "scope_key": left["scope_key"],
                "before": old,
                "after": new,
            }
        )
    return observations


def verify_retention(before, after, *, batch_id, owner_id):
    first = validate_snapshot(before, batch_id=batch_id, owner_id=owner_id)
    second = validate_snapshot(after, batch_id=batch_id, owner_id=owner_id)
    scheduler_clock_evidence(before, after)
    comparison_after = deepcopy(after["tables"])
    for left, right in zip(
        before["tables"]["judge_work"], comparison_after["judge_work"], strict=True
    ):
        if "checked_at" in left:
            right["checked_at"] = left["checked_at"]
    require(
        first == second
        and json.dumps(
            before["tables"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        == json.dumps(
            comparison_after,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
        "complete source ledger/scoring/audit changed across rejected archive",
    )
    return second
