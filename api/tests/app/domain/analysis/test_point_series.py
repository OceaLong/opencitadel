from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.domain.analysis.metrics import METRIC_VERSION
from app.domain.analysis.point_series import point_series
from app.domain.evaluation.summary import EvaluationSnapshot, SummaryRow
from app.domain.evaluation.summary_metrics import complete_cost, derive_snapshot


def known(amount):
    return {
        "calls": 1,
        "token_known": 1,
        "money_known": 1,
        "tokens": 10,
        "money": amount,
        "unresolved": 0,
    }


def record():
    now = datetime(2026, 9, 1, tzinfo=UTC).isoformat()
    row = {
        "id": str(uuid4()),
        "case_id": str(uuid4()),
        "case_label": "case",
        "config_id": str(uuid4()),
        "config_label": "config",
        "repetition": 0,
        "attempt": 1,
        "run_id": str(uuid4()),
        "run_revision": 1,
        "result_revision": 2,
        "score_run_id": None,
        "score_run_revision": None,
        "score_result_revision": None,
        "execution_status": "succeeded",
        "scoring_status": "valid",
        "value": 4,
        "invalidated": False,
        "subject_usage": known("0.20"),
        "judge_usage": known("0.03"),
    }
    return {
        "identity": [
            "agent",
            "dataset",
            "live",
            "environment",
            "rubric",
            METRIC_VERSION,
            "rule",
            "quality",
            ["quality"],
        ],
        "batch_id": str(uuid4()),
        "evaluation_revision": 2,
        "source": "rule",
        "dimension": "quality",
        "rubric_id": str(uuid4()),
        "snapshot_id": str(uuid4()),
        "captured_at": now,
        "usage_watermark": now,
        "row": row,
    }


def test_total_case_result_cost_retains_e11_formula_and_unknown_components():
    def usage(amount):
        return SimpleNamespace(**known(amount))

    assert complete_cost(
        SimpleNamespace(subject_usage=usage("0.20"), judge_usage=usage("0.03"))
    ) == Decimal("0.23")
    assert complete_cost(SimpleNamespace(subject_usage=usage("0"), judge_usage=usage("0"))) == 0
    assert complete_cost(SimpleNamespace(subject_usage=usage("0.20"), judge_usage=None)) is None
    unresolved = usage("0.03")
    unresolved.unresolved = 1
    assert (
        complete_cost(SimpleNamespace(subject_usage=usage("0.20"), judge_usage=unresolved)) is None
    )


def test_retained_series_matches_e11_exact_result_dedup_and_independent_cost_cut():
    item = record()
    item["usage_watermark"] = "2026-09-01T00:00:05+00:00"
    series = point_series([item, deepcopy(item)])[0]
    captured = datetime.fromisoformat(item["captured_at"])
    snapshot = EvaluationSnapshot(
        id=item["snapshot_id"],
        batch_id=item["batch_id"],
        captured_at=captured,
        usage_watermark=item["usage_watermark"],
        expires_at=captured + timedelta(minutes=15),
        evaluation_revision=2,
        source="rule",
        dimension="quality",
        rubric_id=item["rubric_id"],
        rows=(SummaryRow.model_validate(item["row"]),),
        allocations=(),
    )
    expected = derive_snapshot(snapshot)
    assert series["points"] == [p.model_dump(mode="json") for p in expected["points"]]
    assert series["points"][0]["cost_usd"] == "0.23"
    assert len(series["rows"]) == 1
    assert series["cost_basis"] == "case_result_subject_plus_judge"
    assert series["usage_watermark"] != series["captured_at"]


def test_cost_unavailable_preserves_score_and_separates_sources_and_rubrics():
    item = record()
    denied = deepcopy(item)
    denied["row"]["subject_usage"] = None
    denied["row"]["judge_usage"] = None
    series = point_series([denied])[0]
    assert series["points"][0]["value"] == 4
    assert series["points"][0]["cost_usd"] is None
    assert series["quality_cost_metadata"]["sample_count"] == 0
    assert series["quality_cost_metadata"]["missing_count"] == 1
    assert series["distribution_metadata"]["sample_count"] == 1
    other = deepcopy(item)
    other["source"] = "human"
    other["identity"][6] = "human"
    rubric = deepcopy(item)
    rubric["rubric_id"] = str(uuid4())
    assert len(point_series([item, other, rubric])) == 3
    assert point_series(None) is None
    assert point_series([]) == []
