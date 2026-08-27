"""Low-cardinality Prometheus lifecycle metrics for Ops Patrol."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from app.domain.models.patrol import PatrolCheckResult, PatrolFinding, PatrolRun

RUNS = Counter("opencitadel_patrol_runs_total", "Patrol runs", ("status", "trigger_type"))
RUN_DURATION = Histogram("opencitadel_patrol_run_duration_seconds", "Patrol run duration")
CHECK_RESULTS = Counter(
    "opencitadel_patrol_check_results_total", "Patrol check results", ("check_id", "status")
)
FINDINGS = Counter("opencitadel_patrol_findings_total", "Patrol findings", ("severity", "status"))
EVIDENCE_COMPLETENESS = Gauge(
    "opencitadel_patrol_evidence_completeness_ratio", "Latest Patrol evidence completeness"
)
COLLECTOR_ERRORS = Counter(
    "opencitadel_patrol_collector_errors_total", "Collector errors", ("code",)
)


def observe_finalized(
    run: PatrolRun,
    results: list[PatrolCheckResult],
    findings: list[PatrolFinding],
) -> None:
    RUNS.labels(status=run.status.value, trigger_type=run.trigger_type.value).inc()
    if run.duration_ms is not None:
        RUN_DURATION.observe(run.duration_ms / 1000)
    EVIDENCE_COMPLETENESS.set(run.evidence_completeness or 0)
    for result in results:
        CHECK_RESULTS.labels(check_id=result.check_id, status=result.status.value).inc()
        if result.error_code:
            COLLECTOR_ERRORS.labels(code=result.error_code).inc()
    for finding in findings:
        FINDINGS.labels(severity=finding.severity.value, status=finding.status.value).inc()
