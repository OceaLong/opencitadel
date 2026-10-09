"""Capacity arithmetic and fail-closed facade checks without physical measurements.

Coverage boundary: the synthetic transcript below is a sizing/field oracle, not
an acceptance report. A strict positive original-replay, private-copy, and fresh
destination check lives in
``scripts/execution_capacity/test_facade_connection.py``. Public package and
role validation live in ``test_capacity_package.py`` and
``scripts/execution_capacity/test_public_package.py``; fixed-slot frame selection
lives in ``test_capacity_live_resource.py``. None is a full-population or
reference-machine capacity acceptance run.
"""

from copy import deepcopy

import pytest
from capacity_synthetic import transcript, write_transcript
from pydantic import ValidationError
from scripts.acceptance.capacity import (
    BUDGETS_MS,
    FIXTURE_COUNTS,
    derive_capacity_report,
    prepare_capacity_evidence,
    validate_capacity_report,
)
from scripts.acceptance.capacity_derive import budget_errors, percentile
from scripts.acceptance.capacity_models import Environment, Fixture, Network, Protocol
from scripts.execution_capacity.offline_context import PrivateProofError
from test_capacity_package import small_roles


def test_synthetic_public_roles_cannot_claim_a_capacity_report(tmp_path):
    roles, binding = transcript()
    artifacts = write_transcript(tmp_path, roles, binding)
    assert roles["fixture"]["counts"] == FIXTURE_COUNTS
    assert roles["seal"]["cohorts"][0]["source"]["runs"] == 100000
    assert roles["seal"]["cohorts"][0]["source"]["formal_events"] == 10000000
    with pytest.raises(PrivateProofError, match="private C2c original context required"):
        derive_capacity_report(
            artifacts=artifacts,
            binding=binding,
            completed_binding=binding,
            root=tmp_path,
            started_at="2026-09-17T01:00:00Z",
            finished_at="2026-09-17T03:00:00Z",
        )


def test_legacy_or_synthetic_safe_only_report_cannot_validate(tmp_path):
    assert validate_capacity_report({"schema_version": 1}, {}, tmp_path) == [
        "private C2c original context required"
    ]
    roles, binding = transcript()
    artifacts = write_transcript(tmp_path, roles, binding)
    assert validate_capacity_report(
        {"schema_version": 4, "artifacts": artifacts}, binding, tmp_path
    ) == ["private C2c original context required"]


def test_handoff_requires_original_context_before_copy(tmp_path):
    receipt = prepare_capacity_evidence(
        report_path=None,
        fixture_path=None,
        evidence_root=tmp_path / "evidence",
        build={},
        run_id="synthetic",
        project="synthetic",
    )
    assert receipt["errors"] == ["capacity handoff: private C2c original context required"]
    assert "percentiles_ms" not in receipt
    assert not (tmp_path / "evidence" / "capacity").exists()


@pytest.mark.parametrize(
    ("role", "field", "invalid"),
    [
        (Protocol, "schema_version", True),
        (Fixture, "seed", True),
        (Environment, "server_memory_bytes", True),
        (Network, "cdp_delay_ms", False),
    ],
)
def test_public_role_fields_reject_boolean_numeric_values(role, field, invalid):
    raw = deepcopy(
        small_roles()[role.model_fields["role"].default if role is not Fixture else "fixture"]
    )
    role.model_validate(raw)
    raw[field] = invalid
    with pytest.raises(ValidationError):
        role.model_validate(raw)


def _fast_summary():
    return {
        "warm": {name: [1] * 100 for name in BUDGETS_MS["warm"]},
        "cold": {name: [1] * 20 for name in BUDGETS_MS["cold"]},
        "step_capacity": {
            "warm": {name: [1] * 100 for name in ("first_screen", "switch", "history")},
            "cold": {name: [1] * 20 for name in ("first_screen", "history")},
        },
        "latency": {
            "admission_baseline_ms": [100] * 100,
            "admission_loaded_ms": [120] * 100,
        },
        "heap": {"peak_mib": 150, "visible_dom_rows": 150},
        "frames": {"p95_ms": 16, "long_tasks": {"over_200ms_count": 0}},
    }


def test_nearest_rank_and_all_eight_independent_budget_failures():
    assert percentile([1] * 94 + [200, 201, 202, 203, 204, 205], 0.95) == 200
    summary = _fast_summary()
    summary["warm"]["switch"] = [200] * 100
    assert budget_errors(summary) == []

    summary["warm"]["switch"] = [201] * 100
    summary["cold"]["analysis"] = [5001] * 20
    summary["step_capacity"]["warm"]["first_screen"] = [2001] * 100
    summary["heap"] = {"peak_mib": 201, "visible_dom_rows": 201}
    summary["frames"] = {"p95_ms": 34, "long_tasks": {"over_200ms_count": 2}}
    summary["latency"]["admission_loaded_ms"] = [121] * 100
    errors = budget_errors(summary)
    assert len(errors) == 8
    assert any("admission p95 increase" in error for error in errors)
    assert any("frame p95" in error for error in errors)
    assert any("long tasks" in error for error in errors)
