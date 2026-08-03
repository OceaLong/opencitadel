"""Pure deterministic evaluation of Patrol observations."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.domain.models.patrol import (
    PatrolAssertion,
    PatrolAssertionResult,
    PatrolCheck,
    PatrolCheckStatus,
    PatrolEvaluatedCheck,
    PatrolFindingSeverity,
    PatrolObservationSubmission,
    PatrolProbeStatus,
)


_MISSING = object()


def _read_field(document: dict[str, Any], field: str) -> Any:
    current: Any = document
    for segment in field[2:].split("."):
        if not isinstance(current, dict) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _compare(assertion: PatrolAssertion, observed: Any, now: datetime) -> bool:
    if observed is _MISSING:
        return False
    op = assertion.op
    expected = assertion.value
    if op == "empty":
        return observed in (None, "", [], {}, ())
    if op == "not_empty":
        return observed not in (None, "", [], {}, ())
    if op in {"gt", "gte", "lt", "lte"}:
        if isinstance(observed, bool) or isinstance(expected, bool):
            return False
        if type(observed) is not type(expected) or not isinstance(observed, (int, float)):
            return False
        return {
            "gt": observed > expected,
            "gte": observed >= expected,
            "lt": observed < expected,
            "lte": observed <= expected,
        }[op]
    if op == "eq":
        return type(observed) is type(expected) and observed == expected
    if op == "ne":
        return type(observed) is type(expected) and observed != expected
    if op == "in":
        return isinstance(expected, list) and observed in expected
    if op == "not_in":
        return isinstance(expected, list) and observed not in expected
    if op == "contains":
        return isinstance(observed, (str, list, dict)) and expected in observed
    if op == "regex":
        return isinstance(observed, str) and isinstance(expected, str) and re.search(expected, observed) is not None
    if op in {"age_lt_seconds", "age_lte_seconds"}:
        parsed = _parse_time(observed)
        if parsed is None or not isinstance(expected, (int, float)) or isinstance(expected, bool):
            return False
        age = max(0.0, (now - parsed).total_seconds())
        return age < expected if op == "age_lt_seconds" else age <= expected
    return False


class PatrolAssertionEngine:
    @staticmethod
    def evaluate(
        check: PatrolCheck,
        submission: PatrolObservationSubmission | None,
        *,
        now: datetime | None = None,
    ) -> PatrolEvaluatedCheck:
        reference_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not check.enabled:
            return PatrolEvaluatedCheck(
                check_id=check.id,
                status=PatrolCheckStatus.SKIPPED,
                severity=PatrolFindingSeverity.INFO,
                evidence_complete=True,
            )
        if submission is None:
            return PatrolEvaluatedCheck(
                check_id=check.id,
                status=PatrolCheckStatus.ERROR,
                severity=PatrolFindingSeverity.WARNING,
                error_code="RESULT_MISSING",
                error_message="Enabled check did not submit a result",
            )
        if submission.check_id != check.id:
            raise ValueError("submission check_id does not match check")

        evidence_types = {item.type for item in submission.evidence_refs if item.verified}
        evidence_complete = set(check.required_evidence).issubset(evidence_types)
        if submission.probe_status != PatrolProbeStatus.OK:
            return PatrolEvaluatedCheck(
                check_id=check.id,
                status=PatrolCheckStatus.ERROR,
                severity=PatrolFindingSeverity.WARNING,
                observed=submission.observation,
                evidence_refs=submission.evidence_refs,
                explanation=submission.explanation,
                error_code=submission.error_code or "PROBE_FAILED",
                error_message=submission.error_message,
                evidence_complete=evidence_complete,
            )
        if not evidence_complete:
            return PatrolEvaluatedCheck(
                check_id=check.id,
                status=PatrolCheckStatus.ERROR,
                severity=PatrolFindingSeverity.WARNING,
                observed=submission.observation,
                evidence_refs=submission.evidence_refs,
                explanation=submission.explanation,
                error_code="EVIDENCE_INCOMPLETE",
                error_message="Required evidence is missing or failed verification",
                evidence_complete=False,
            )

        results: list[PatrolAssertionResult] = []
        worst = PatrolCheckStatus.PASS
        for assertion in check.assertions:
            observed = _read_field(submission.observation, assertion.field)
            passed = _compare(assertion, observed, reference_time)
            if not passed:
                candidate = (
                    PatrolCheckStatus.FAIL
                    if assertion.status_on_failure == "fail"
                    else PatrolCheckStatus.WARN
                )
                if candidate == PatrolCheckStatus.FAIL or worst == PatrolCheckStatus.PASS:
                    worst = candidate
            results.append(
                PatrolAssertionResult(
                    assertion_id=assertion.id,
                    field=assertion.field,
                    op=assertion.op,
                    expected=assertion.value,
                    observed=None if observed is _MISSING else observed,
                    passed=passed,
                    status_on_failure=assertion.status_on_failure,
                    message=assertion.message,
                )
            )

        severity = PatrolFindingSeverity.INFO
        if worst == PatrolCheckStatus.WARN:
            severity = PatrolFindingSeverity.WARNING
        elif worst == PatrolCheckStatus.FAIL:
            severity = PatrolFindingSeverity(check.severity_on_fail)
        return PatrolEvaluatedCheck(
            check_id=check.id,
            status=worst,
            severity=severity,
            observed=submission.observation,
            assertion_results=results,
            evidence_refs=submission.evidence_refs,
            explanation=submission.explanation,
            evidence_complete=True,
        )
