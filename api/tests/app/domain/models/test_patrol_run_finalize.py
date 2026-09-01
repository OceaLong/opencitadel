"""Domain enrichment: PatrolRun.finalize state machine."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.domain.models.patrol import (
    PatrolCheckStatus,
    PatrolEvaluatedCheck,
    PatrolFindingSeverity,
    PatrolRun,
    PatrolRunStatus,
    PatrolTriggerType,
)


def _run(started_at: datetime) -> PatrolRun:
    return PatrolRun(
        pack_id="pack-1",
        execution_run_id=uuid4(),
        pack_version=1,
        pack_snapshot={"config": {"target_ref": "test", "checks": []}},
        trigger_type=PatrolTriggerType.MANUAL,
        idempotency_key="idem-1",
        started_at=started_at,
    )


def _check(
    check_id: str, status: PatrolCheckStatus, *, evidence_complete: bool
) -> PatrolEvaluatedCheck:
    return PatrolEvaluatedCheck(
        check_id=check_id,
        status=status,
        severity=PatrolFindingSeverity.WARNING,
        evidence_complete=evidence_complete,
    )


def test_finalize_all_pass_completes_cleanly() -> None:
    started = datetime(2026, 9, 1, 10, tzinfo=UTC)
    now = started + timedelta(seconds=3)
    run = _run(started)

    results = [
        _check("a", PatrolCheckStatus.PASS, evidence_complete=True),
        _check("b", PatrolCheckStatus.PASS, evidence_complete=True),
    ]

    returned = run.finalize(results, now)

    assert returned is run
    assert run.status == PatrolRunStatus.COMPLETED
    assert run.pass_count == 2
    assert run.warn_count == 0
    assert run.fail_count == 0
    assert run.error_count == 0
    assert run.evidence_completeness == 1.0
    assert run.finished_at == now
    assert run.duration_ms == 3000
    assert run.summary["counts"] == {"pass": 2, "warn": 0, "fail": 0, "error": 0, "skipped": 0}


def test_finalize_with_findings_marks_completed_with_findings() -> None:
    started = datetime(2026, 9, 1, 10, tzinfo=UTC)
    run = _run(started)

    results = [
        _check("a", PatrolCheckStatus.PASS, evidence_complete=True),
        _check("b", PatrolCheckStatus.FAIL, evidence_complete=True),
        _check("c", PatrolCheckStatus.WARN, evidence_complete=False),
        _check("d", PatrolCheckStatus.ERROR, evidence_complete=False),
    ]

    run.finalize(results, started + timedelta(seconds=1))

    assert run.status == PatrolRunStatus.COMPLETED_WITH_FINDINGS
    assert run.pass_count == 1
    assert run.fail_count == 1
    assert run.warn_count == 1
    assert run.error_count == 1
    # 4 enabled (non-skipped) checks, 2 with complete evidence.
    assert run.evidence_completeness == 0.5


def test_finalize_excludes_skipped_from_evidence_denominator() -> None:
    started = datetime(2026, 9, 1, 10, tzinfo=UTC)
    run = _run(started)

    results = [
        _check("a", PatrolCheckStatus.PASS, evidence_complete=True),
        _check("b", PatrolCheckStatus.SKIPPED, evidence_complete=False),
    ]

    run.finalize(results, started + timedelta(seconds=1))

    assert run.status == PatrolRunStatus.COMPLETED
    assert run.skipped_count == 1
    # Only the single enabled check counts; it is complete -> 1.0.
    assert run.evidence_completeness == 1.0


def test_finalize_all_skipped_defaults_completeness_to_one() -> None:
    started = datetime(2026, 9, 1, 10, tzinfo=UTC)
    run = _run(started)

    results = [_check("a", PatrolCheckStatus.SKIPPED, evidence_complete=False)]

    run.finalize(results, started + timedelta(seconds=1))

    assert run.status == PatrolRunStatus.COMPLETED
    assert run.evidence_completeness == 1.0
