from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from time import perf_counter

import pytest
import yaml

from app.application.patrol_templates import load_patrol_template
from app.domain.models.patrol import (
    PatrolEvidenceRef,
    PatrolObservationSubmission,
    PatrolProbeStatus,
)
from app.domain.services.patrol_assertion_engine import PatrolAssertionEngine


FIXTURES = Path(__file__).parents[4] / "deploy" / "patrol-demo" / "fixtures"

# This suite replays PatrolAssertionEngine (a pure, read-only check
# classifier) against synthesized observations and asserts the result
# matches each fixture's expected.json -- it never simulates a write or a
# before/after remediation transition. Fixtures that describe a remediation
# scenario (identified by the presence of the "remediation" key in
# expected.json, e.g. 21-remediation-crashloop) are out of scope for this
# replay: their expected_write_operations is intentionally nonzero (an
# actuator patch call), which conflicts with the zero-write invariant this
# suite enforces for the read-only golden set, and the engine has no
# representation of "the check goes from fail to pass because the actuator
# patched the workload". test_golden_catalog_is_complete_and_zero_write still
# requires every fixture directory (golden or remediation) to have a matched
# setup.yaml/expected.json pair with a unique case_id, so a fixture added
# without registering the pair -- or wrongly tagged/untagged for this
# exclusion -- still fails loudly.
GOLDEN_EXPECTED_PATHS = [
    path
    for path in sorted(FIXTURES.glob("*/expected.json"))
    if "remediation" not in json.loads(path.read_text())
]

HEALTHY = {
    "k8s-workload-availability": {"unavailable_replicas": 0, "not_ready_workloads": []},
    "k8s-restart-spike": {"restart_count_1h": 0},
    "k8s-pending-failed": {"pending_pods": [], "failed_jobs": [], "stale_cronjobs": []},
    "k8s-warning-events": {"high_risk_count": 0},
    "k8s-resource-pressure": {"node_pressures": [], "max_pvc_utilization_percent": 10.0},
    "app-error-rate": {"value": 0.0},
    "tls-expiry": {"days_remaining": 90},
    "backup-freshness": {"age_hours": 1.0},
    "dependency-health": {"healthy": True},
    "endpoint-health": {"healthy": True, "latency_ms": 10},
}

OVERRIDES = {
    "02-unavailable-replica": {"k8s-workload-availability": {"unavailable_replicas": 1, "not_ready_workloads": ["deployment/fixture-unavailable"]}},
    "03-crashloop": {
        "k8s-workload-availability": {"unavailable_replicas": 1, "not_ready_workloads": ["deployment/fixture-crashloop"]},
        "k8s-restart-spike": {"restart_count_1h": 11},
    },
    "04-image-pull": {"k8s-pending-failed": {"pending_pods": [{"ref": "pod/fixture-image-pull", "reason": "ImagePullBackOff"}], "failed_jobs": [], "stale_cronjobs": []}},
    "05-failed-scheduling": {"k8s-warning-events": {"high_risk_count": 1}},
    "06-restarts-warn": {"k8s-restart-spike": {"restart_count_1h": 4}},
    "07-restarts-fail": {"k8s-restart-spike": {"restart_count_1h": 11}},
    "08-failed-job": {"k8s-pending-failed": {"pending_pods": [], "failed_jobs": ["job/fixture-failed-job"], "stale_cronjobs": []}},
    "09-stale-cronjob": {"k8s-pending-failed": {"pending_pods": [], "failed_jobs": [], "stale_cronjobs": ["cronjob/fixture-stale-cronjob"]}},
}


def _evidence(check) -> list[PatrolEvidenceRef]:
    return [
        PatrolEvidenceRef(
            type=item,
            ref=f"collector://evidence/golden/{item}",
            sha256="a" * 64,
            target_ref="opencitadel-local",
            verified=True,
        )
        for item in check.required_evidence
    ]


def _fixture_state(case_id: str) -> dict:
    setup = FIXTURES / case_id / "setup.yaml"
    for document in yaml.safe_load_all(setup.read_text()):
        if document and document.get("kind") == "ConfigMap" and document.get("metadata", {}).get("name") == "patrol-fixture-state":
            return json.loads(document["data"]["state.json"])
    return {}


def _state_observations(case_id: str) -> dict:
    state = _fixture_state(case_id)
    if not state:
        return {}
    pressure = state.get("node_pressure")
    return {
        "k8s-resource-pressure": {
            "node_pressures": ([{"ref": "node/demo", "condition": pressure}] if pressure else []),
            "max_pvc_utilization_percent": float(state.get("pvc_percent", 10)),
        },
        "app-error-rate": {"value": float(state.get("app_5xx_ratio", 0))},
        "tls-expiry": {"days_remaining": int(state.get("tls_days_remaining", 90))},
        "backup-freshness": {"age_hours": float(state.get("backup_age_hours", 1))},
        "dependency-health": {"healthy": bool(state.get("dependency_healthy", True))},
        "endpoint-health": {"healthy": bool(state.get("endpoint_healthy", True)), "latency_ms": 10},
    }


def _evaluate(case_id: str):
    pack = load_patrol_template("kubernetes-baseline-v1")
    observed = deepcopy(HEALTHY)
    observed.update(_state_observations(case_id))
    observed.update(OVERRIDES.get(case_id, {}))
    results = {}
    for check in pack.checks:
        if case_id == "19-collector-error" and check.id == "k8s-workload-availability":
            submission = PatrolObservationSubmission(
                check_id=check.id,
                probe_status=PatrolProbeStatus.UNAVAILABLE,
                error_code="UPSTREAM_TIMEOUT",
                error_message="fixture timeout",
            )
        else:
            evidence_refs = _evidence(check)
            if case_id == "20-prompt-injection" and check.id == "k8s-workload-availability":
                evidence_refs.append(
                    PatrolEvidenceRef(
                        type="log_excerpt",
                        ref="collector://evidence/golden/prompt-injection-log",
                        sha256="b" * 64,
                        target_ref="opencitadel-local",
                        verified=True,
                    )
                )
            submission = PatrolObservationSubmission(
                check_id=check.id,
                observation=observed[check.id],
                evidence_refs=evidence_refs,
            )
        results[check.id] = PatrolAssertionEngine.evaluate(check, submission)
    return results


def _matches_expected(results, expected: dict) -> bool:
    for row in expected["expected_results"]:
        actual = results[row["check_id"]]
        if actual.status.value != row["status"]:
            return False
        if "severity" in row and actual.severity.value != row["severity"]:
            return False
        if "error_code" in row and actual.error_code != row["error_code"]:
            return False
        if "assertion_id" in row and not any(
            item.assertion_id == row["assertion_id"] and item.passed is False
            for item in actual.assertion_results
        ):
            return False
    return True


def score_fixture_catalog() -> dict:
    case_scores = []
    evidence_total = 0
    evidence_complete = 0
    for expected_path in GOLDEN_EXPECTED_PATHS:
        expected = json.loads(expected_path.read_text())
        started = perf_counter()
        results = _evaluate(expected["case_id"])
        duration_ms = (perf_counter() - started) * 1000
        relevant = [item for item in results.values() if item.status.value != "error"]
        evidence_total += len(relevant)
        evidence_complete += sum(item.evidence_complete for item in relevant)
        actual_evidence = {
            evidence.type
            for item in results.values()
            for evidence in item.evidence_refs
        }
        case_scores.append(
            {
                "case_id": expected["case_id"],
                "matched": _matches_expected(results, expected),
                "evidence_matched": set(expected["required_evidence"]) <= actual_evidence,
                "duration_ms": round(duration_ms, 3),
            }
        )

    healthy = _evaluate("01-healthy")
    false_positives = sum(item.status.value != "pass" for item in healthy.values())
    durations = sorted(item["duration_ms"] for item in case_scores)
    p95_index = max(0, (95 * len(durations) + 99) // 100 - 1)
    return {
        "total_cases": len(case_scores),
        "expected_result_matches": sum(item["matched"] for item in case_scores),
        "injected_fault_detections": sum(item["matched"] for item in case_scores if item["case_id"] != "01-healthy"),
        "false_positives": false_positives,
        "evidence_completeness": evidence_complete / evidence_total if evidence_total else 0.0,
        "required_evidence_case_matches": sum(item["evidence_matched"] for item in case_scores),
        "assertion_replay_p95_seconds": round(durations[p95_index] / 1000, 6),
        "cases": case_scores,
    }


@pytest.mark.parametrize("expected_path", GOLDEN_EXPECTED_PATHS, ids=lambda path: path.parent.name)
def test_golden_fixture_matches_server_authoritative_classification(expected_path: Path):
    expected = json.loads(expected_path.read_text())
    case_id = expected["case_id"]
    results = _evaluate(case_id)
    assert _matches_expected(results, expected)
    for row in expected["expected_results"]:
        actual = results[row["check_id"]]
        assert actual.status.value == row["status"]
        if "severity" in row:
            assert actual.severity.value == row["severity"]
        if "error_code" in row:
            assert actual.error_code == row["error_code"]
        if "assertion_id" in row:
            assertion = next(item for item in actual.assertion_results if item.assertion_id == row["assertion_id"])
            assert assertion.passed is False
    assert all(item.evidence_complete for item in results.values() if item.status.value != "error")
    actual_evidence = {evidence.type for item in results.values() for evidence in item.evidence_refs}
    assert set(expected["required_evidence"]) <= actual_evidence


def test_golden_catalog_is_complete_and_zero_write():
    # Every fixture directory -- golden (read-only) or remediation -- must
    # carry a matched expected.json/setup.yaml pair with a unique case_id.
    # This catches "added a fixture directory but forgot one of the two
    # files" (or duplicated a case_id) regardless of which category it's in.
    expected_files = sorted(FIXTURES.glob("*/expected.json"))
    setup_files = sorted(FIXTURES.glob("*/setup.yaml"))
    assert len(expected_files) == len(setup_files)
    payloads = [json.loads(path.read_text()) for path in expected_files]
    assert len({item["case_id"] for item in payloads}) == len(payloads)

    # The read-only golden set's count is still hardcoded so that adding (or
    # mis-tagging) a fixture forces a deliberate update here, same as before
    # this suite learned to exclude remediation fixtures.
    assert len(GOLDEN_EXPECTED_PATHS) == 20
    golden_payloads = [json.loads(path.read_text()) for path in GOLDEN_EXPECTED_PATHS]
    assert len({item["case_id"] for item in golden_payloads}) == 20
    assert all(item["expected_write_operations"] == 0 for item in golden_payloads)
    prompt_case = next(item for item in golden_payloads if item["case_id"] == "20-prompt-injection")
    assert {"delete", "patch", "create", "update"}.issubset(prompt_case["forbidden_tool_calls"])


def test_fixture_score_is_computed_from_actual_server_classifications():
    score = score_fixture_catalog()
    assert score["total_cases"] == 20
    assert score["expected_result_matches"] == 20
    assert score["injected_fault_detections"] == 19
    assert score["false_positives"] == 0
    assert score["evidence_completeness"] == 1.0
    assert score["required_evidence_case_matches"] == 20
