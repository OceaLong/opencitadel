from decimal import Decimal
from uuid import uuid4

from app.application.services.execution_export_rows import BATCH_COLUMNS, batch_row
from app.domain.evaluation.summary import SummaryRow, UsageSummary


def test_batch_export_uses_fixed_row_usage_and_preserves_unknown_total():
    row = SummaryRow(
        id=uuid4(),
        case_id=uuid4(),
        config_id=uuid4(),
        case_label="=formula",
        config_label="fixed",
        repetition=1,
        attempt=1,
        run_id=None,
        run_revision=None,
        result_revision=1,
        score_run_id=None,
        score_run_revision=None,
        score_result_revision=None,
        execution_status="pending",
        scoring_status="pending",
        value=None,
        invalidated=False,
        subject_usage=UsageSummary(
            calls=1, token_known=1, money_known=0, tokens=12, money=None, unresolved=1
        ),
        judge_usage=None,
    )
    metadata = {
        "source": "rule",
        "dimension": "pass",
        "rubric_id": str(uuid4()),
        "evaluation_revision": 2,
        "usage_watermark": "2026-09-16T00:00:00Z",
        "dataset_version": str(uuid4()),
        "execution_mode": "recorded",
    }
    exported = batch_row(row, metadata)
    assert set(exported) == {c.name for c in BATCH_COLUMNS}
    assert exported["value"] is None
    assert exported["value_missing"] is True
    assert exported["cost_usd"] is None
    assert exported["cost_complete"] is False
    assert exported["subject_tokens"] == 12
    assert exported["judge_calls"] is None
    assert exported["judge_usage_present"] is False
    assert exported["case_label"] == "=formula"


def test_observed_zero_cost_remains_numeric_zero():
    row = SummaryRow(
        id=uuid4(),
        case_id=uuid4(),
        config_id=uuid4(),
        case_label="case",
        config_label="config",
        repetition=1,
        attempt=1,
        run_id=None,
        run_revision=None,
        result_revision=1,
        score_run_id=None,
        score_run_revision=None,
        score_result_revision=None,
        execution_status="completed",
        scoring_status="scored",
        value=0,
        invalidated=False,
        subject_usage=UsageSummary(
            calls=0, token_known=0, money_known=0, tokens=0, money="0", unresolved=0
        ),
        judge_usage=UsageSummary(
            calls=0, token_known=0, money_known=0, tokens=0, money="0", unresolved=0
        ),
    )
    metadata = {
        "source": "human",
        "dimension": "score",
        "rubric_id": str(uuid4()),
        "evaluation_revision": 1,
        "usage_watermark": "2026-09-16T00:00:00Z",
        "dataset_version": str(uuid4()),
        "execution_mode": "recorded",
    }
    exported = batch_row(row, metadata)
    assert exported["cost_usd"] == Decimal(0)
    assert exported["cost_complete"] is True
    assert exported["value"] == 0
    assert exported["value_missing"] is False


def test_extracted_e11_derivation_retains_case_weight_and_missing_cost_semantics():
    from datetime import UTC, datetime, timedelta

    from app.domain.evaluation.summary import EvaluationSnapshot
    from app.domain.evaluation.summary_metrics import derive_snapshot

    now = datetime.now(UTC)
    case_id, config_id = uuid4(), uuid4()
    common = {
        "case_id": case_id,
        "config_id": config_id,
        "case_label": "case",
        "config_label": "config",
        "repetition": 1,
        "attempt": 1,
        "run_id": None,
        "run_revision": None,
        "result_revision": 1,
        "score_run_id": None,
        "score_run_revision": None,
        "score_result_revision": None,
        "execution_status": "completed",
        "scoring_status": "scored",
        "invalidated": False,
    }
    usage = UsageSummary(calls=0, token_known=0, money_known=0, tokens=0, money="0", unresolved=0)
    rows = (
        SummaryRow(id=uuid4(), value=0, subject_usage=usage, judge_usage=usage, **common),
        SummaryRow(id=uuid4(), value=None, subject_usage=None, judge_usage=None, **common),
    )
    snapshot = EvaluationSnapshot(
        id=uuid4(),
        batch_id=uuid4(),
        captured_at=now,
        usage_watermark=now,
        expires_at=now + timedelta(minutes=15),
        evaluation_revision=3,
        source="human",
        dimension="score",
        rubric_id=uuid4(),
        rows=rows,
        allocations=(),
    )
    result = derive_snapshot(snapshot)
    assert result["distribution_metadata"]["sample_count"] == 1
    assert result["distribution_metadata"]["missing_count"] == 0
    assert result["quality_cost_metadata"]["sample_count"] == 1
    assert result["quality_cost_metadata"]["missing_count"] == 1
    assert result["points"][0].cost_usd == "0"
    assert result["points"][1].cost_usd is None
    assert result["subject_usage"]["money"] == "0"
    assert result["scoring_counts"] == {"scored": 2}


def test_run_export_projects_safe_fixed_fields_and_actual_configuration():
    from app.application.services.execution_export_rows import RUN_COLUMNS, run_row

    fixed = {
        "run_id": str(uuid4()),
        "run_fact": {
            "family": "agent",
            "purpose": "subject",
            "execution_mode": "recorded",
            "admission_configuration_id": "actual",
            "configuration_revision": "filter-echo",
            "status": "completed",
            "admitted_at": "2026-09-16T00:00:00Z",
            "terminal_at": "2026-09-16T00:00:01Z",
            "private_input": "SECRET",
        },
        "coverage": {
            "state": "partial",
            "missing_fields": ["activity"],
            "missing_intervals": [],
            "internal_event_hash": "SECRET",
        },
        "interval_fact": {"activity_occupancy_ms": 0, "activity_samples": 1, "activity_missing": 0},
        "approval_fact": None,
        "usage": [],
        "scores": [
            {
                "result_id": "result",
                "private_reason": "SECRET",
                "source_sets": [],
                "scores": [
                    {"source": "human", "dimension": "quality", "value": 3, "reason": "SECRET"}
                ],
            }
        ],
    }
    row = run_row(fixed, cut="opaque")
    assert set(row) == {column.name for column in RUN_COLUMNS}
    assert row["configuration_revision"] == "actual"
    assert row["activity_occupancy_ms"] == 0
    assert row["approval_wait_ms"] is None
    assert "SECRET" not in str(row)
