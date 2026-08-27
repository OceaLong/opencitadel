from uuid import uuid4

from app.application.patrol_templates import load_patrol_template
from app.domain.models.patrol import (
    PatrolCheckResult,
    PatrolCheckStatus,
    PatrolFinding,
    PatrolFindingSeverity,
    PatrolRun,
    PatrolTriggerType,
)
from app.interfaces.endpoints.patrol_routes import _finding_allowed_actions
from app.interfaces.schemas.patrol import PatrolFindingResponse
from tests.app.openapi_test_support import app


def _run_with_config():
    config = load_patrol_template("kubernetes-baseline-v1")
    return PatrolRun(
        pack_id="pack-1",
        execution_run_id=uuid4(),
        pack_version=1,
        pack_snapshot={"config": config.model_dump(mode="json")},
        trigger_type=PatrolTriggerType.MANUAL,
        idempotency_key="key-1",
    )


def _check_result(run: PatrolRun, check_id: str) -> PatrolCheckResult:
    return PatrolCheckResult(
        run_id=run.id,
        check_id=check_id,
        status=PatrolCheckStatus.FAIL,
        severity=PatrolFindingSeverity.CRITICAL,
        fingerprint="f" * 64,
    )


def _finding_for(run: PatrolRun, check_result: PatrolCheckResult) -> PatrolFinding:
    return PatrolFinding(
        run_id=run.id,
        check_result_id=check_result.id,
        fingerprint=check_result.fingerprint,
        severity=PatrolFindingSeverity.CRITICAL,
        title="finding",
        summary="summary",
    )


def test_finding_allowed_actions_returns_all_actuator_actions_for_k8s_probe():
    # "k8s-workload-availability" carries the k8s_workload_summary probe --
    # a k8s_* tool has an Actuator counterpart for every remediation action.
    run = _run_with_config()
    check_result = _check_result(run, "k8s-workload-availability")
    finding = _finding_for(run, check_result)

    assert _finding_allowed_actions(finding, [check_result], run) == [
        "restart_workload",
        "rollback_workload",
        "scale_workload",
    ]


def test_finding_allowed_actions_empty_for_non_actuator_probe():
    # "endpoint-health" carries the http_probe probe -- closed-world catalog
    # says only k8s_* probes have an Actuator counterpart today.
    run = _run_with_config()
    check_result = _check_result(run, "endpoint-health")
    finding = _finding_for(run, check_result)

    assert _finding_allowed_actions(finding, [check_result], run) == []


def test_finding_allowed_actions_empty_when_check_result_missing():
    run = _run_with_config()
    check_result = _check_result(run, "k8s-workload-availability")
    finding = _finding_for(run, check_result)

    # The Finding's check_result_id isn't present in the supplied list --
    # fail closed (empty), don't crash the response assembly.
    assert _finding_allowed_actions(finding, [], run) == []


def test_patrol_finding_response_from_domain_embeds_allowed_actions():
    run = _run_with_config()
    check_result = _check_result(run, "k8s-workload-availability")
    finding = _finding_for(run, check_result)

    response = PatrolFindingResponse.from_domain(
        finding, allowed_actions=_finding_allowed_actions(finding, [check_result], run)
    )

    assert response.allowed_actions == [
        "restart_workload",
        "rollback_workload",
        "scale_workload",
    ]


def test_patrol_finding_response_from_domain_accepts_explicit_empty_allowed_actions():
    run = _run_with_config()
    check_result = _check_result(run, "endpoint-health")
    finding = _finding_for(run, check_result)

    assert PatrolFindingResponse.from_domain(finding, allowed_actions=[]).allowed_actions == []


def test_openapi_exposes_complete_patrol_endpoint_set():
    paths = set(app.openapi()["paths"])
    assert {
        "/api/patrol-packs",
        "/api/patrol-packs/{pack_id}",
        "/api/patrol-packs/{pack_id}/metrics",
        "/api/patrol-packs/{pack_id}/validate",
        "/api/patrol-packs/{pack_id}/activate",
        "/api/patrol-packs/{pack_id}/pause",
        "/api/patrol-packs/{pack_id}/trigger",
        "/api/patrol-runs",
        "/api/patrol-runs/{run_id}",
        "/api/patrol-runs/{run_id}/evidence",
        "/api/patrol-runs/{run_id}/cancel",
        "/api/patrol-runs/{run_id}/replay",
        "/api/patrol-findings/{finding_id}/acknowledge",
        "/api/patrol-findings/{finding_id}/resolve",
        "/api/patrol-findings/{finding_id}/false-positive",
    }.issubset(paths)
