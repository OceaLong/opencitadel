from copy import deepcopy
from uuid import uuid4

import pytest
from scripts.acceptance.protected_retention import validate_snapshot, verify_retention


def evidence():
    batch, subject, judge, result, intent, call, namespace, source_set = [
        str(uuid4()) for _ in range(8)
    ]
    owner, scope = "owned-user", "user:owned-user"

    def row(**fields):
        return {"scope_key": scope, **fields}

    tables = {
        "batches": [
            row(id=batch, status="completed_with_errors", cleanup_status="clean", revision=5)
        ],
        "results": [row(id=result, batch_id=batch, unknown_effect=False, recovery_pending=False)],
        "attempts": [row(result_id=result, run_id=subject, status="settled")],
        "intents": [
            row(id=intent, batch_id=batch, result_id=result, run_id=judge, namespace_id=namespace)
        ],
        "judge_work": [row(intent_id=intent, status="settled")],
        "invalidations": [
            row(intent_id=intent, batch_id=batch, run_id=judge, source_set_id=source_set)
        ],
        "bindings": [
            row(run_id=subject, namespace_id=batch, purpose="evaluation_subject"),
            row(run_id=judge, namespace_id=namespace, purpose="evaluation_judge"),
        ],
        "namespaces": [
            row(
                id=batch,
                state="closed",
                body={"mode": "isolated", "environment_version": str(uuid4())},
            ),
            row(id=namespace, state="open"),
        ],
        "reservations": [
            row(
                call_identity=call,
                state="unknown",
                settlement=None,
                settled_at=None,
                demand={
                    "scope": scope,
                    "requester": owner,
                    "batch_id": batch,
                    "purpose": "evaluation_judge",
                    "tokens": 4096,
                    "money": "0.1",
                    "buckets": [
                        {"key": f"5:batch:{namespace}"},
                        {"key": f"6:purpose:{batch}:evaluation_judge"},
                    ],
                },
            )
        ],
        "dispatches": [
            row(
                call_identity=call,
                run_id=judge,
                activity_id="judge-call",
                request_snapshot_sha256="a" * 64,
            )
        ],
        "settlements": [],
        "execution_leases": [
            row(run_id=subject, phase="released", state={}),
            row(
                run_id=judge,
                phase="released",
                state={"failure_code": "NON_IDEMPOTENT_OUTCOME_UNKNOWN"},
            ),
        ],
        "environment_leases": [
            row(id=str(uuid4()), state="verified_clean", case_slot={"batch_id": batch})
        ],
        "score_sets": [row(id=source_set, batch_id=batch, result_id=result, source="model")],
        "scores": [
            row(
                id=str(uuid4()),
                set_id=source_set,
                status="error",
                value=None,
                reason="judge_execution_failed",
                evidence=[{"resource_id": judge}],
            )
        ],
        "batch_events": [row(id=str(uuid4()), batch_id=batch, revision=5, kind="completed")],
        "events": [
            {
                "owner_scope_key": scope,
                "stream_id": rid,
                "event_id": str(uuid4()),
                "stream_version": 1,
                "prev_hash": "0" * 64,
                "event_hash": "a" * 64,
                "public_payload": {},
                "internal_payload": {},
            }
            for rid in (subject, judge)
        ],
        "audits": [],
        "tasks": [
            {
                "owner_user_id": owner,
                "team_id": None,
                "run_id": judge,
                "activity_id": "judge-call",
                "status": "unknown",
                "failure_code": "NON_IDEMPOTENT_OUTCOME_UNKNOWN",
            }
        ],
        "projections": [
            {
                "owner_user_id": owner,
                "team_id": None,
                "run_id": rid,
                "terminal": True,
                "active_activity_count": 0,
            }
            for rid in (subject, judge)
        ],
        "reviews": [],
        "archives": [],
        "buckets": [
            {
                "key": f"5:batch:{namespace}",
                "slots": 0,
                "reserved_tokens": 4096,
                "reserved_money": "0.1",
            },
            {
                "key": f"6:purpose:{batch}:evaluation_judge",
                "slots": 1,
                "reserved_tokens": 4096,
                "reserved_money": "0.1",
            },
        ],
    }
    return {
        "schema_version": 1,
        "batch_id": batch,
        "owner_id": owner,
        "batch_scope": scope,
        "read_only": True,
        "tables": tables,
    }


def validate(value):
    return validate_snapshot(value, batch_id=value["batch_id"], owner_id=value["owner_id"])


def test_verified_retention_preserves_unknown_and_explicit_future_obligation():
    before = evidence()
    result = verify_retention(
        before, deepcopy(before), batch_id=before["batch_id"], owner_id=before["owner_id"]
    )
    assert result["future_obligation"] == "open"
    assert result["local_active_sends"] == 0
    assert result["settled"] is False
    assert result["archived"] is False
    assert before["tables"]["reservations"][0]["state"] == "unknown"


@pytest.mark.parametrize(
    "table",
    [
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
        "execution_leases",
        "environment_leases",
        "score_sets",
        "scores",
        "batch_events",
        "events",
        "projections",
        "buckets",
    ],
)
def test_missing_required_source_refuses_retention(table):
    value = evidence()
    value["tables"][table] = []
    with pytest.raises(ValueError, match="protected retention:"):
        validate(value)


@pytest.mark.parametrize(
    "table",
    ["reservations", "dispatches", "invalidations", "execution_leases", "scores", "batch_events"],
)
def test_foreign_scope_source_refuses_retention(table):
    value = evidence()
    value["tables"][table][0]["scope_key"] = "user:another-user"
    with pytest.raises(ValueError, match="protected retention:"):
        validate(value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v.update(read_only=False),
        lambda v: v["tables"]["batches"][0].update(cleanup_status="pending"),
        lambda v: v["tables"]["batches"][0].update(status="running"),
        lambda v: v["tables"]["reservations"][0].update(state="reserved"),
        lambda v: v["tables"]["reservations"][0].update(settlement={}),
        lambda v: v["tables"]["judge_work"][0].update(status="submitted"),
        lambda v: v["tables"]["environment_leases"][0].update(state="held"),
        lambda v: v["tables"]["execution_leases"][1].update(state={}),
        lambda v: v["tables"]["projections"][1].update(terminal=False),
        lambda v: v["tables"]["projections"][1].update(active_activity_count=1),
        lambda v: v["tables"]["tasks"][0].update(status="call_started"),
        lambda v: v["tables"]["scores"][0].update(value=3),
        lambda v: v["tables"]["archives"].append(
            {"scope_key": v["batch_scope"], "resource_id": v["batch_id"]}
        ),
        lambda v: v["tables"]["invalidations"][0].update(source_set_id=str(uuid4())),
        lambda v: v["tables"]["dispatches"][0].update(run_id=str(uuid4())),
        lambda v: v["tables"]["buckets"][0].update(reserved_tokens=0),
        lambda v: v["tables"]["reviews"].append(
            {"scope_key": v["batch_scope"], "status": "processing"}
        ),
    ],
)
def test_unsafe_incomplete_or_incorrect_snapshot_refuses_retention(mutation):
    value = evidence()
    mutation(value)
    with pytest.raises(ValueError, match="protected retention:"):
        validate(value)


@pytest.mark.parametrize(
    "table",
    [
        "reservations",
        "dispatches",
        "invalidations",
        "execution_leases",
        "scores",
        "score_sets",
        "events",
        "batch_events",
        "namespaces",
        "buckets",
    ],
)
def test_entire_protected_source_must_remain_equal_across_archive_rejection(table):
    before = evidence()
    after = deepcopy(before)
    after["tables"][table][0]["changed"] = "mutation"
    with pytest.raises(ValueError, match="protected retention:"):
        verify_retention(before, after, batch_id=before["batch_id"], owner_id=before["owner_id"])


def test_complete_scoring_and_audit_payloads_are_compared_not_only_hashes():
    before = evidence()
    after = deepcopy(before)
    after["tables"]["events"][0]["internal_payload"] = {"different": True}
    with pytest.raises(ValueError, match="protected retention:"):
        verify_retention(before, after, batch_id=before["batch_id"], owner_id=before["owner_id"])
    after = deepcopy(before)
    after["tables"]["scores"][0]["evidence"][0]["resource_id"] = str(uuid4())
    with pytest.raises(ValueError, match="protected retention:"):
        verify_retention(before, after, batch_id=before["batch_id"], owner_id=before["owner_id"])


def test_typed_immutable_payload_rejects_integer_to_boolean_change_with_same_hash():
    before = evidence()
    before["tables"]["events"][0]["internal_payload"] = {"nested": {"value": 1}}
    after = deepcopy(before)
    after["tables"]["events"][0]["internal_payload"]["nested"]["value"] = True
    assert before["tables"]["events"][0]["event_hash"] == after["tables"]["events"][0]["event_hash"]
    with pytest.raises(ValueError, match="protected retention:"):
        verify_retention(before, after, batch_id=before["batch_id"], owner_id=before["owner_id"])


def test_shared_bucket_must_hold_sum_of_every_unknown_reservation():
    value = evidence()
    reservation = deepcopy(value["tables"]["reservations"][0])
    dispatch = deepcopy(value["tables"]["dispatches"][0])
    reservation["call_identity"] = dispatch["call_identity"] = str(uuid4())
    value["tables"]["reservations"].append(reservation)
    value["tables"]["dispatches"].append(dispatch)
    with pytest.raises(ValueError, match="unknown conservative budget hold released"):
        validate(value)
    for bucket in value["tables"]["buckets"]:
        bucket["reserved_tokens"] = 8192
        bucket["reserved_money"] = "0.2"
    assert validate(value)["future_obligation"] == "open"


def test_recorded_empty_environment_is_complete_authoritative_mode_proof():
    value = evidence()
    value["tables"]["namespaces"][0]["body"] = {"mode": "recorded", "environment_version": None}
    value["tables"]["environment_leases"] = []
    assert validate(value)["environment_status"] == "clean"


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "unknown", "environment_version": None},
        {"mode": "recorded", "environment_version": str(uuid4())},
        {"mode": "recorded"},
        {"mode": "isolated", "environment_version": str(uuid4())},
    ],
)
def test_empty_environment_without_complete_recorded_authority_is_rejected(body):
    value = evidence()
    value["tables"]["namespaces"][0]["body"] = body
    value["tables"]["environment_leases"] = []
    with pytest.raises(ValueError, match="protected retention:"):
        validate(value)


def test_closed_namespace_can_preserve_real_dispatching_unknown_obligations():
    value = evidence()
    value["tables"]["reservations"][0]["state"] = "dispatching"
    value["tables"]["namespaces"][1]["state"] = "closed"
    assert validate(value)["future_obligation"] == "open"
    assert (
        verify_retention(
            value, deepcopy(value), batch_id=value["batch_id"], owner_id=value["owner_id"]
        )["settled"]
        is False
    )


def test_namespace_state_change_across_two_valid_snapshots_is_rejected():
    before = evidence()
    after = deepcopy(before)
    after["tables"]["namespaces"][1]["state"] = "closed"
    validate(after)
    with pytest.raises(ValueError, match="source ledger/scoring/audit changed"):
        verify_retention(before, after, batch_id=before["batch_id"], owner_id=before["owner_id"])


def test_closed_namespace_cannot_hide_released_held_budget():
    value = evidence()
    value["tables"]["namespaces"][1]["state"] = "closed"
    value["tables"]["buckets"][0]["reserved_tokens"] = 0
    with pytest.raises(ValueError, match="conservative budget hold released"):
        validate(value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v["tables"]["execution_leases"][1].update(state={"status": "completed"}),
        lambda v: v["tables"]["scores"][0].update(value=3),
        lambda v: v["tables"]["buckets"][0].update(reserved_tokens=4095),
        lambda v: v["tables"]["tasks"][0].update(status="call_started"),
        lambda v: v["tables"]["tasks"][0].update(status="succeeded"),
        lambda v: v["tables"]["tasks"][0].update(activity_id="another-call"),
        lambda v: v["tables"]["tasks"][0].update(failure_code=None),
        lambda v: v["tables"]["namespaces"][1].update(state="unknown"),
    ],
)
def test_dispatching_retention_requires_complete_independent_unknown_proof(mutation):
    value = evidence()
    value["tables"]["reservations"][0]["state"] = "dispatching"
    value["tables"]["namespaces"][1]["state"] = "closed"
    mutation(value)
    with pytest.raises(ValueError, match="protected retention:"):
        validate(value)


def early_invalidation_evidence():
    value = evidence()
    intent = value["tables"]["intents"][0]
    value["tables"]["invalidations"][0].update(source_set_id=None, evaluation_revision=2)
    value["tables"]["score_sets"][0].update(
        request_id="judge:" + intent["id"], evaluation_revision=3, status="failed"
    )
    return value


def test_invalidation_before_original_null_source_is_verified_by_exact_intent_and_revision():
    value = early_invalidation_evidence()
    assert validate(value)["future_obligation"] == "open"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v["tables"]["score_sets"][0].update(request_id="judge:other-intent"),
        lambda v: v["tables"]["score_sets"][0].update(evaluation_revision=2),
        lambda v: v["tables"]["score_sets"][0].update(status="complete"),
        lambda v: v["tables"]["invalidations"][0].update(evaluation_revision=None),
    ],
)
def test_nullable_invalidation_requires_original_error_source_and_real_order(mutation):
    value = early_invalidation_evidence()
    mutation(value)
    with pytest.raises(ValueError, match="incomplete invalidation history"):
        validate(value)


def test_scheduler_check_timestamp_can_advance_without_changing_retained_source():
    before = evidence()
    before["tables"]["judge_work"][0]["checked_at"] = "2026-10-08T08:20:00+00:00"
    after = deepcopy(before)
    after["tables"]["judge_work"][0]["checked_at"] = "2026-10-08T08:20:01+00:00"
    assert (
        verify_retention(before, after, batch_id=before["batch_id"], owner_id=before["owner_id"])[
            "future_obligation"
        ]
        == "open"
    )
    assert (
        after["tables"]["judge_work"][0]["checked_at"]
        != before["tables"]["judge_work"][0]["checked_at"]
    )


@pytest.mark.parametrize(
    ("before_clock", "after_clock"),
    [
        ("2026-10-08T08:20:01+00:00", "2026-10-08T08:20:00+00:00"),
        ("2026-10-08T08:20:00", "2026-10-08T08:20:01+00:00"),
        ("not-a-timestamp", "not-a-timestamp"),
        (1, True),
        (None, "2026-10-08T08:20:01+00:00"),
        ("2026-10-08T08:20:00+00:00", None),
    ],
)
def test_scheduler_clock_exception_rejects_rollback_missing_authority_or_type_change(
    before_clock, after_clock
):
    before = evidence()
    before["tables"]["judge_work"][0]["checked_at"] = before_clock
    after = deepcopy(before)
    after["tables"]["judge_work"][0]["checked_at"] = after_clock
    with pytest.raises(ValueError, match="protected retention:"):
        verify_retention(before, after, batch_id=before["batch_id"], owner_id=before["owner_id"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v["tables"]["judge_work"][0].update(status="stopped"),
        lambda v: v["tables"]["judge_work"][0].update(evaluation_revision=4),
        lambda v: v["tables"]["judge_work"][0].update(error="changed"),
        lambda v: v["tables"]["batches"][0].update(claim_until=None),
        lambda v: v["tables"]["judge_work"][0].pop("checked_at"),
    ],
)
def test_scheduler_clock_exception_never_hides_other_mutations(mutation):
    before = evidence()
    before["tables"]["judge_work"][0]["checked_at"] = "2026-10-08T08:20:00+00:00"
    after = deepcopy(before)
    after["tables"]["judge_work"][0]["checked_at"] = "2026-10-08T08:20:01+00:00"
    mutation(after)
    with pytest.raises(ValueError, match="protected retention:"):
        verify_retention(before, after, batch_id=before["batch_id"], owner_id=before["owner_id"])
