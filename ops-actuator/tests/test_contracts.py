"""Mirrors ops-collector/tests/test_contracts.py, adapted for
ActuatorErrorCode / ActuatorEnvelope's action + action_outcome + before/after
shape and the actuator://evidence/ ref prefix.
"""

from datetime import UTC, datetime

from opencitadel_ops_actuator.contracts import ActuatorEnvelope, ActuatorErrorCode, evidence_for


def test_error_enum_is_complete():
    assert {item.value for item in ActuatorErrorCode} == {
        "NAMESPACE_DENIED",
        "TARGET_DENIED",
        "TARGET_NOT_FOUND",
        "KIND_MISMATCH",
        "REPLICAS_OUT_OF_BOUNDS",
        "IDEMPOTENCY_KEY_MISSING",
        "NO_ROLLBACK_REVISION",
        "K8S_ERROR",
        "TIMEOUT",
        "OUTPUT_TOO_LARGE",
        "INTERNAL",
    }


def test_response_envelope_contains_trace_action_and_evidence_contract():
    data = {"namespace": "demo", "workload": "api", "kind": "deployment"}
    item = ActuatorEnvelope(
        target_ref="demo",
        action="restart_workload",
        action_outcome="applied",
        before={"replicas": 3},
        after={"replicas": 3, "restarted_at": "2026-08-04T00:00:00+00:00"},
        data=data,
        evidence=evidence_for("demo", data, ["summary"]),
    )
    assert item.request_id
    assert item.executed_at.tzinfo is not None
    assert item.evidence[0].target_ref == "demo"
    assert item.evidence[0].ref.startswith("actuator://evidence/")
    assert len(item.evidence[0].sha256) == 64
    assert item.evidence[0].expires_at > datetime.now(UTC)


def test_failed_outcome_allows_absent_before_after():
    item = ActuatorEnvelope(
        target_ref="demo",
        action="restart_workload",
        action_outcome="failed",
        error_code=ActuatorErrorCode.NAMESPACE_DENIED,
        error_message="NAMESPACE_DENIED",
    )
    assert item.before is None
    assert item.after is None
