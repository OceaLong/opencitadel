"""The effect journal cannot become a second Run lifecycle or retry authority."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.domain.execution.effect import EffectEvidenceRef, EffectObservation, EffectOperationSpec
from app.domain.execution.run import InvalidRunTransitionError, RunAggregate
from tests.app.domain.execution.test_universal_run import NOW, RUN_ID, command, decide_and_replay


def harness(*, dispatch=True):
    aggregate, events, activity = RunAggregate(), [], uuid4()

    def send(name, payload=None):
        return decide_and_replay(aggregate, events, command(name, len(events), payload))

    send(
        "CreateRun",
        {
            "family": "agent",
            "source_entity_type": "session",
            "source_entity_id": "s",
            "semantic_payload": {},
        },
    )
    send("StartRun")
    send(
        "RequestActivity",
        {
            "activity_id": str(activity),
            "activity_type": "tool.call",
            "timeout_at": (NOW + timedelta(hours=1)).isoformat(),
            "input_digest": "a" * 64,
        },
    )
    spec = EffectOperationSpec(
        operation_id=uuid4(),
        invocation_id=uuid4(),
        run_id=RUN_ID,
        activity_id=activity,
        generation=0,
        provider_identity="provider",
        contract_digest="a" * 64,
        arguments_digest="b" * 64,
        target_digest="c" * 64,
        idempotency_key="stable-key",
        capability="manual_only",
    )
    send("PrepareEffectOperation", {"spec": spec.model_dump(mode="json")})
    if dispatch:
        send(
            "StartEffectDispatch",
            {
                "operation_id": str(spec.operation_id),
                "expected_revision": 1,
                "dispatch_proof": {
                    "activity_id": str(spec.activity_id),
                    "request_generation": 0,
                    "claim_generation": 1,
                    "worker_id": "worker",
                },
            },
        )
    return send, spec, events


def observation(state, *, result=False, source="provider_receipt"):
    return EffectObservation(
        receipt_id=uuid4(),
        state=state,
        source=source,
        evidence=EffectEvidenceRef(ref="execution/results/evidence.json", sha256="a" * 64),
        observed_at=NOW,
        result_ref="execution/results/result.json" if result else None,
        result_digest="b" * 64 if result else None,
    ).model_dump(mode="json")


def payload(spec, revision, value):
    return {
        "operation_id": str(spec.operation_id),
        "expected_revision": revision,
        "observation": value,
    }


def unknown(send, spec):
    send(
        "RecordEffectObservation",
        payload(spec, 2, observation("unknown", source="transport_unknown")),
    )
    return send(
        "MarkActivityOutcomeUnknown",
        {"activity_id": str(spec.activity_id), "generation": 0, "failure_code": "UNKNOWN"},
    )


def test_unknown_waits_without_run_retry_and_send_is_single_use():
    send, spec, _ = harness()
    state = unknown(send, spec)
    assert state.status == "waiting"
    assert state.wait_reason == "external_outcome_unknown"
    for name, value in [
        ("ResumeRun", {}),
        ("FailRun", {"failure_code": "x", "retryable": True}),
        (
            "StartEffectDispatch",
            {
                "operation_id": str(spec.operation_id),
                "expected_revision": 3,
                "dispatch_proof": {
                    "activity_id": str(spec.activity_id),
                    "request_generation": 0,
                    "claim_generation": 1,
                    "worker_id": "worker",
                },
            },
        ),
    ]:
        with pytest.raises(InvalidRunTransitionError):
            send(name, value)


def test_applied_requires_result_then_resumes_exact_original_activity_once():
    send, spec, _ = harness()
    unknown(send, spec)
    state = send("ResolveActivityOutcome", payload(spec, 3, observation("applied")))
    assert state.status == "waiting"
    assert state.wait_reason == "result_material_missing"
    assert state.effect_operations[0].awaiting_result
    value = observation("applied", result=True, source="human_verified")
    state = send("SupplyActivityResult", payload(spec, 4, value))
    assert state.status == "running"
    assert state.settled_activities == ((spec.activity_id, "succeeded", 0),)
    assert state.activity_failure_codes == ()
    state = send("SupplyActivityResult", payload(spec, 4, value))
    assert len(state.activity_results) == 1


def test_cancelled_run_records_late_fact_without_resurrection():
    send, spec, _ = harness()
    unknown(send, spec)
    send("CancelRun")
    state = send("ResolveActivityOutcome", payload(spec, 3, observation("applied", result=True)))
    assert state.status == "cancelled"
    assert state.activity_results == ()
    assert state.effect_operations[0].state == "applied"


def test_conflicting_receipt_preserves_confirmed_fact_and_flags_integrity():
    send, spec, _ = harness()
    unknown(send, spec)
    send("ResolveActivityOutcome", payload(spec, 3, observation("not_applied")))
    state = send("ResolveActivityOutcome", payload(spec, 4, observation("applied", result=True)))
    assert state.effect_operations[0].state == "not_applied"
    assert state.effect_operations[0].integrity_conflict
    assert state.settled_activities == ((spec.activity_id, "failed", 0),)


@pytest.mark.parametrize("name", ["ResolveActivityOutcome", "SupplyActivityResult"])
def test_conflict_quarantine_cannot_resume_when_matching_result_arrives(name):
    send, spec, events = harness()
    unknown(send, spec)
    send("ResolveActivityOutcome", payload(spec, 3, observation("applied")))
    send("ResolveActivityOutcome", payload(spec, 4, observation("not_applied")))
    before = len(events)
    state = send(name, payload(spec, 5, observation("applied", result=True)))
    assert state.effect_operations[0].integrity_conflict
    assert state.status == "waiting"
    assert state.activity_results == ()
    assert not {"ActivityCompleted", "RunResumed"} & {event.event_type for event in events[before:]}


def test_late_conflict_stops_live_run_and_cannot_be_retried():
    send, spec, _ = harness()
    unknown(send, spec)
    send("ResolveActivityOutcome", payload(spec, 3, observation("applied", result=True)))
    state = send("ResolveActivityOutcome", payload(spec, 4, observation("not_applied")))
    assert state.status == "waiting"
    for name, body in (
        ("ResumeRun", {}),
        ("FailRun", {"failure_code": "x", "retryable": True}),
    ):
        with pytest.raises(InvalidRunTransitionError):
            send(name, body)
    assert send("CancelRun").status == "cancelled"


def test_conflicting_business_result_cannot_replace_original_applied_result():
    send, spec, _ = harness()
    unknown(send, spec)
    first = observation("applied", result=True)
    send("ResolveActivityOutcome", payload(spec, 3, first))
    changed = {
        **observation("applied", result=True),
        "result_ref": "execution/results/changed.json",
        "result_digest": "c" * 64,
    }
    state = send("ResolveActivityOutcome", payload(spec, 4, changed))
    assert state.status == "waiting"
    effect = state.effect_operations[0]
    assert effect.integrity_conflict
    assert effect.result_ref == first["result_ref"]
    assert effect.latest_receipt.result_digest == first["result_digest"]


def test_unknown_cannot_be_overwritten_by_ordinary_worker_failure():
    send, spec, _ = harness()
    for name, value in [
        ("FailActivity", {"failure_code": "TIMEOUT"}),
        ("CompleteActivity", {"result_ref": "empty"}),
    ]:
        with pytest.raises(InvalidRunTransitionError):
            send(name, {"activity_id": str(spec.activity_id), "generation": 0, **value})


def test_abandon_stops_recovery_without_changing_outcome_or_usage():
    send, spec, events = harness()
    unknown(send, spec)
    state = send(
        "AbandonEffectResolution",
        {
            "operation_id": str(spec.operation_id),
            "expected_revision": 3,
            "reason": "No further evidence",
        },
    )
    assert state.effect_operations[0].abandoned
    assert state.effect_operations[0].state == "unknown"
    assert state.status == "waiting"
    assert not any("Usage" in event.event_type for event in events)


def test_new_operation_for_same_invocation_rejected_even_with_new_key():
    send, spec, _ = harness()
    changed = spec.model_copy(update={"operation_id": uuid4(), "idempotency_key": "new-key"})
    with pytest.raises(InvalidRunTransitionError):
        send("PrepareEffectOperation", {"spec": changed.model_dump(mode="json")})


def test_prepared_but_never_authorized_send_resolves_with_only_kernel_proof():
    send, spec, _ = harness(dispatch=False)
    send(
        "MarkActivityOutcomeUnknown",
        {"activity_id": str(spec.activity_id), "generation": 0, "failure_code": "LOST_WORKER"},
    )
    with pytest.raises(InvalidRunTransitionError):
        send("ResolveActivityOutcome", payload(spec, 1, observation("not_applied")))
    value = payload(spec, 1, observation("not_applied", source="kernel_not_sent"))
    value["request_id"] = str(uuid4())
    state = send("ResolveActivityOutcome", value)
    assert state.effect_operations[0].state == "not_applied"
    assert state.status == "running"
    assert state.settled_activities == ((spec.activity_id, "failed", 0),)
