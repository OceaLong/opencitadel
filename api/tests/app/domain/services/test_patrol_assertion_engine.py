from __future__ import annotations

from datetime import datetime, timezone

from app.domain.models.patrol import (
    PatrolAssertion,
    PatrolCheck,
    PatrolEvidenceRef,
    PatrolObservationSubmission,
    PatrolProbe,
)
from app.domain.services.patrol_assertion_engine import PatrolAssertionEngine


def check(*, enabled: bool = True, failure: str = "fail") -> PatrolCheck:
    return PatrolCheck(
        id="availability",
        title="Availability",
        enabled=enabled,
        probe=PatrolProbe(tool="k8s_workload_summary", args={}, output_schema_hash="v1"),
        assertions=[PatrolAssertion(id="a", field="$.count", op="eq", value=0, status_on_failure=failure, message="bad")],
        severity_on_fail="critical",
        required_evidence=["summary"],
    )


def evidence(*, verified: bool = True) -> PatrolEvidenceRef:
    return PatrolEvidenceRef(type="summary", ref="collector://e/1", sha256="a" * 64, verified=verified)


def submission(value=0, **kwargs) -> PatrolObservationSubmission:
    return PatrolObservationSubmission(check_id="availability", observation={"count": value}, evidence_refs=[evidence()], **kwargs)


def test_all_healthy_assertions_produce_pass() -> None:
    assert PatrolAssertionEngine.evaluate(check(), submission()).status.value == "pass"


def test_warn_failure_produces_warn() -> None:
    assert PatrolAssertionEngine.evaluate(check(failure="warn"), submission(1)).status.value == "warn"


def test_fail_is_more_severe_than_warn() -> None:
    target = check(failure="warn")
    target.assertions.append(PatrolAssertion(id="b", field="$.count", op="lt", value=0, status_on_failure="fail", message="bad"))
    assert PatrolAssertionEngine.evaluate(target, submission(1)).status.value == "fail"


def test_probe_error_never_produces_pass() -> None:
    result = PatrolAssertionEngine.evaluate(check(), submission(probe_status="error", error_code="UPSTREAM_TIMEOUT"))
    assert result.status.value == "error"


def test_disabled_check_is_skipped() -> None:
    assert PatrolAssertionEngine.evaluate(check(enabled=False), None).status.value == "skipped"


def test_missing_required_evidence_downgrades_pass_to_error() -> None:
    item = submission()
    item.evidence_refs = []
    assert PatrolAssertionEngine.evaluate(check(), item).error_code == "EVIDENCE_INCOMPLETE"


def test_bad_evidence_hash_produces_error() -> None:
    item = submission()
    item.evidence_refs[0].verified = False
    assert PatrolAssertionEngine.evaluate(check(), item).status.value == "error"


def test_numeric_comparison_does_not_coerce_strings() -> None:
    target = check()
    target.assertions[0] = PatrolAssertion(id="a", field="$.count", op="gte", value=0, message="bad")
    assert PatrolAssertionEngine.evaluate(target, submission("1")).status.value == "fail"


def test_age_operator_uses_explicit_reference_time() -> None:
    target = check()
    target.assertions[0] = PatrolAssertion(id="a", field="$.time", op="age_lte_seconds", value=60, message="old")
    item = submission()
    item.observation = {"time": "2026-08-03T00:00:00Z"}
    result = PatrolAssertionEngine.evaluate(target, item, now=datetime(2026, 8, 3, 0, 1, tzinfo=timezone.utc))
    assert result.status.value == "pass"


def test_agent_supplied_status_is_ignored() -> None:
    assert PatrolAssertionEngine.evaluate(check(), submission(1, agent_status="pass")).status.value == "fail"
