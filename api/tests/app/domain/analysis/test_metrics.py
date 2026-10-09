from datetime import UTC, datetime
from decimal import Decimal

import pytest


def test_parallel_work_and_case_repeats_are_not_double_counted():
    from app.domain.analysis.metrics import case_weighted_mean, interval_union_ms

    assert interval_union_ms([(0, 10), (5, 20), (4, 8), (25, 30)]) == 25
    assert case_weighted_mean([("a", 4.0), ("a", 4.0), ("b", 0.0)]) == 2.0
    assert case_weighted_mean([]) is None
    assert interval_union_ms([]) == 0
    with pytest.raises(ValueError, match="negative interval"):
        interval_union_ms([(3, 2)])


def test_nonfinite_scores_are_rejected_not_silently_weighted():
    from app.domain.analysis.metrics import case_weighted_mean

    for value in [float("nan"), float("inf")]:
        with pytest.raises(ValueError, match="nonfinite score"):
            case_weighted_mean([("a", value)])


def test_run_denominators_and_invalid_latency_are_explicit():
    from app.domain.analysis.metrics import execution_metrics

    result = execution_metrics(
        [
            {"run_id": "a", "status": "completed", "duration_ms": 10},
            {"run_id": "b", "status": "failed", "duration_ms": 20},
            {"run_id": "c", "status": "cancelled", "duration_ms": 100},
            {"run_id": "d", "status": "running", "duration_ms": None},
            {"run_id": "e", "status": "unknown", "duration_ms": None},
            {"run_id": "f", "status": "completed", "duration_ms": -1},
        ]
    )
    assert result["success_rate"].value == 2 / 3
    assert result["success_rate"].denominator == 3
    assert result["latency_p50"].value == 15
    assert result["latency_p95"].value == 19.5
    assert result["latency_p50"].sample_count == 2
    assert result["latency_p50"].missing_count == 1
    assert result["latency_p50"].numerator is None
    assert result["cancelled"].value == result["pending"].value == result["unknown"].value == 1
    assert execution_metrics([])["success_rate"].value is None


def test_physical_usage_dedup_partial_zero_and_purpose_are_preserved():
    from app.domain.analysis.metrics import usage_metrics

    rows = [
        {
            "call_identity": "a",
            "purpose": "production",
            "input_tokens": 10,
            "output_tokens": 2,
            "cost_usd": Decimal(0),
        },
        {
            "call_identity": "b",
            "purpose": "production",
            "input_tokens": 8,
            "output_tokens": None,
            "cost_usd": None,
        },
        {
            "call_identity": "c",
            "purpose": "evaluation_judge",
            "input_tokens": 100,
            "output_tokens": 4,
            "cost_usd": Decimal(".2"),
        },
    ]
    result = usage_metrics([*rows, rows[0]])
    assert result["production"]["input_tokens"].value == 18
    assert result["production"]["output_tokens"].value == 2
    assert result["production"]["token_coverage"].value == 0.5
    assert result["production"]["cost_coverage"].value == 0.5
    assert result["production"]["cost_usd"].value == 0
    assert result["production"]["cost_usd"].missing_count == 1
    assert result["evaluation_judge"]["cost_usd"].value == Decimal(".2")
    with pytest.raises(ValueError, match="conflicting fixed identity"):
        usage_metrics([*rows, {**rows[0], "input_tokens": 11}])


def test_attempt_union_tool_work_and_terminal_business_failure():
    from app.domain.analysis.metrics import attempt_metrics

    rows = [
        {
            "attempt_id": "a",
            "kind": "tool",
            "started_ms": 0,
            "ended_ms": 10,
            "status": "completed",
            "business_outcome": "failed",
        },
        {"attempt_id": "b", "kind": "tool", "started_ms": 5, "ended_ms": 20, "status": "failed"},
        {
            "attempt_id": "c",
            "kind": "tool",
            "started_ms": 22,
            "ended_ms": None,
            "status": "unknown",
        },
        {
            "attempt_id": "d",
            "kind": "tool",
            "started_ms": None,
            "ended_ms": None,
            "status": "queued",
        },
    ]
    result = attempt_metrics(rows, start_ms=0, cut_ms=30)
    assert result["activity_occupancy_ms"].value == 20
    assert result["tool_work_ms"].value == 25
    assert result["tool_error_rate"].value == 1
    assert result["tool_error_rate"].denominator == 2
    assert result["tool_unknown"].value == 1
    assert result["activity_occupancy_ms"].missing_count == 1


def test_approval_overlaps_open_cut_and_missing_request():
    from app.domain.analysis.metrics import approval_metrics

    result = approval_metrics(
        [
            {"approval_id": "a", "status": "pending", "occurred_ms": 0},
            {"approval_id": "b", "status": "pending", "occurred_ms": 5},
            {"approval_id": "a", "status": "approved", "occurred_ms": 10},
            {"approval_id": "c", "status": "expired", "occurred_ms": 12},
        ],
        start_ms=0,
        cut_ms=20,
    )
    assert result.value == 20
    assert result.missing_count == 1
    assert result.sample_count == 2


def test_calendar_days_and_repeated_hours_use_utc_identity():
    from app.domain.analysis.metrics import calendar_bucket, resolve_timezone

    assert resolve_timezone("America/New_York", "Asia/Shanghai") == "America/New_York"
    first = calendar_bucket(datetime(2026, 11, 1, 5, 30, tzinfo=UTC), "hour", "America/New_York")
    second = calendar_bucket(datetime(2026, 11, 1, 6, 30, tzinfo=UTC), "hour", "America/New_York")
    assert first != second
    day = calendar_bucket(datetime(2026, 3, 8, 10, tzinfo=UTC), "day", "America/New_York")
    assert (day[1] - day[0]).total_seconds() == 23 * 3600


def test_actual_waiting_approval_and_zero_events_coverage():
    from app.domain.analysis.metrics import approval_metrics

    result = approval_metrics(
        [{"approval_id": "a", "status": "waiting", "occurred_ms": 5}], start_ms=0, cut_ms=20
    )
    assert result.value == 15


def test_source_set_required_optional_and_composite_pending_are_distinct():
    from app.domain.analysis.score_metrics import score_metrics

    rows = [
        {
            "case_id": "a",
            "result_id": "a1",
            "config_id": "c",
            "execution_status": "succeeded",
            "required": {"rule": ["rule:0"], "model": ["quality"]},
            "applicable": {"rule": ["quality"], "model": ["quality"]},
            "thresholds": {"model": {"quality": 3}},
            "scores": [
                {"source": "rule", "dimension": "rule:0", "value": True, "status": "valid"},
                {"source": "rule", "dimension": "rule:1", "value": False, "status": "valid"},
                {"source": "model", "dimension": "quality", "value": 4, "status": "valid"},
            ],
        },
        {
            "case_id": "a",
            "result_id": "a2",
            "config_id": "c",
            "execution_status": "succeeded",
            "required": {"rule": ["rule:0"], "model": ["quality"]},
            "applicable": {"rule": ["quality"], "model": ["quality"]},
            "thresholds": {"model": {"quality": 3}},
            "scores": [{"source": "rule", "dimension": "rule:0", "value": True, "status": "valid"}],
        },
        {
            "case_id": "b",
            "result_id": "b1",
            "config_id": "c",
            "execution_status": "failed",
            "required": {"rule": ["rule:0"]},
            "applicable": {"rule": ["quality"]},
            "thresholds": {},
            "scores": [],
        },
    ]
    result = score_metrics(rows)
    assert result["required_rule_pass_rate"].value == 1
    assert result["required_rule_pass_rate"].denominator == 1
    assert result["required_rule_pass_rate"].excluded_count == 1
    assert result["confirmed_pass_rate"].value == 0.25
    assert result["confirmed_pass_rate"].denominator == 2
    assert result["confirmed_pass_rate"].missing_count == 1
    assert result["model:quality:mean"].value == 4
    assert result["model:quality:mean"].numerator is None
    assert result["model:quality:coverage"].value == 0.5


def test_invalidated_model_is_excluded_without_resurrecting_previous_score():
    from app.domain.analysis.score_metrics import score_metrics

    result = score_metrics(
        [
            {
                "case_id": "a",
                "result_id": "a1",
                "config_id": "c",
                "execution_status": "succeeded",
                "required": {"model": ["quality"]},
                "applicable": {"model": ["quality"]},
                "thresholds": {"model": {"quality": 3}},
                "scores": [
                    {
                        "source": "model",
                        "dimension": "quality",
                        "status": "valid",
                        "value": 4,
                        "invalidated": True,
                    }
                ],
            }
        ]
    )
    assert result["model:quality:mean"].value is None
    assert result["model:quality:mean"].excluded_count == 1
    assert result["confirmed_pass_rate"].value == 0
    assert result["confirmed_pass_rate"].missing_count == 1


def test_missing_source_set_metadata_never_confirms_success():
    from app.domain.analysis.score_metrics import score_metrics

    result = score_metrics(
        [
            {
                "case_id": "a",
                "result_id": "a",
                "config_id": "c",
                "execution_status": "succeeded",
                "required": {},
                "applicable": {},
                "thresholds": {},
                "scores": [],
                "required_complete": False,
            }
        ]
    )
    assert result["confirmed_pass_rate"].value == 0
    assert result["confirmed_pass_rate"].missing_count == 1


def test_score_summary_pairs_only_common_cases_and_keeps_optional_applicability():
    from app.domain.analysis.score_summary import score_summary

    records = []
    for config, case, value in [("x", "a", 4), ("x", "b", 0), ("y", "a", 2), ("y", "c", 0)]:
        records.append(
            {
                "family": "agent",
                "dataset_version": "d1",
                "mode": "recorded",
                "environment_version": "recorded",
                "rubric": "r1",
                "evaluation_revision": 2,
                "result_id": config + case,
                "case_id": case,
                "config_id": config,
                "execution_status": "succeeded",
                "required_conditions": [],
                "source_sets": [
                    {
                        "source": "model",
                        "required_dimensions": [],
                        "applicable_dimensions": ["quality"],
                        "source_set_id": config + case,
                    }
                ],
                "scores": [
                    {
                        "source": "model",
                        "dimension": "quality",
                        "value": value,
                        "status": "valid",
                        "invalidated": False,
                        "source_set_id": config + case,
                    }
                ],
            }
        )
    result = score_summary(records)
    comparison = result["comparisons"][0]
    assert comparison["case_count"] == 1
    assert comparison["mean_left"] == 4
    assert comparison["mean_right"] == 2
    assert comparison["delta"] == 2
    assert comparison["confidence_interval"] is None
    assert result["series"][0]["metrics"]["confirmed_pass_rate"]["value"] == 0


def test_tool_error_categories_do_not_double_count_attempt_rate():
    from app.domain.analysis.metrics import attempt_metrics

    result = attempt_metrics(
        [
            {
                "attempt_id": "a",
                "kind": "tool",
                "status": "failed",
                "business_outcome": "failure",
                "started_ms": 0,
                "ended_ms": 10,
            },
            {
                "attempt_id": "b",
                "kind": "tool",
                "status": "completed",
                "business_outcome": "success",
                "started_ms": 0,
                "ended_ms": 10,
            },
        ],
        start_ms=0,
        cut_ms=20,
    )
    assert result["tool_error_rate"].value == 0.5
    assert result["tool_execution_errors"].value == 1
    assert result["tool_business_errors"].value == 1


def test_score_summary_retains_each_batch_evaluation_cut():
    from app.domain.analysis.score_summary import score_summary

    rows = [
        {
            "family": "agent",
            "dataset_version": "d",
            "mode": "recorded",
            "environment_version": "recorded",
            "rubric": "r",
            "batch_id": batch,
            "evaluation_revision": revision,
            "result_id": batch,
            "case_id": batch,
            "config_id": "c",
            "execution_status": "succeeded",
            "required_conditions": [],
            "source_sets": [
                {"source": "rule", "required_dimensions": [], "applicable_dimensions": []}
            ],
            "scores": [],
        }
        for batch, revision in [("a", 2), ("b", 7)]
    ]
    assert score_summary(rows)["evaluation_cuts"] == [
        {"batch_id": "a", "evaluation_revision": 2},
        {"batch_id": "b", "evaluation_revision": 7},
    ]


def persisted_source_record(case, applicable, *, human_metadata=True):
    """E07 rule IDs are independent of the E09 rubric applicability metadata."""
    sets = [
        {"source": "rule", "required_dimensions": ["rule:0"], "applicable_dimensions": applicable}
    ]
    scores = [
        {
            "source": "rule",
            "dimension": "rule:0",
            "value": True,
            "status": "valid",
            "invalidated": False,
        },
        {
            "source": "rule",
            "dimension": "rule:1",
            "value": False,
            "status": "valid",
            "invalidated": False,
        },
        {
            "source": "rule",
            "dimension": "rule:2",
            "value": None,
            "status": "not_evaluable",
            "invalidated": False,
        },
    ]
    if human_metadata:
        sets.append(
            {
                "source": "human",
                "required_dimensions": [d for d in applicable if d == "correctness"],
                "applicable_dimensions": applicable,
            }
        )
        scores.extend(
            {"source": "human", "dimension": d, "value": 4, "status": "valid", "invalidated": False}
            for d in applicable
        )
    return {
        "family": "agent",
        "dataset_version": "dataset",
        "mode": "recorded",
        "environment_version": "recorded",
        "rubric": "rubric",
        "result_id": case,
        "case_id": case,
        "config_id": "config",
        "execution_status": "succeeded",
        "required_conditions": [{"source": "human", "dimension_id": "correctness", "minimum": 3}],
        "source_sets": sets,
        "scores": scores,
    }


def test_persisted_mixed_rubric_applicability_excludes_inapplicable_requirements():
    from app.domain.analysis.score_summary import score_summary

    result = score_summary(
        [
            persisted_source_record("a", ["completeness"]),
            persisted_source_record("b", ["correctness"]),
        ]
    )
    assert len(result["series"]) == 2
    for row in result["series"]:
        metric = row["metrics"]["confirmed_pass_rate"]
        assert metric["value"] == 1
        assert metric["missing_count"] == 0
    missing = score_summary([persisted_source_record("c", ["correctness"], human_metadata=False)])
    assert missing["series"][0]["metrics"]["confirmed_pass_rate"]["value"] == 0
    assert missing["series"][0]["metrics"]["confirmed_pass_rate"]["missing_count"] == 1


def test_persisted_e07_rule_heads_keep_optional_and_not_evaluable_descriptives():
    from app.domain.analysis.score_summary import score_summary

    result = score_summary([persisted_source_record("a", ["completeness"])])
    row = result["series"][0]
    metrics = row["metrics"]
    assert metrics["rule:rule:0:mean"]["value"] == 1
    assert metrics["rule:rule:1:mean"]["value"] == 0
    assert metrics["rule:rule:1:coverage"]["value"] == 1
    assert metrics["rule:rule:2:mean"]["value"] is None
    assert metrics["rule:rule:2:mean"]["missing_count"] == 1
    assert metrics["rule:rule:2:coverage"]["value"] == 0
    assert "rule:completeness:mean" not in metrics
    assert metrics["required_rule_pass_rate"]["value"] == 1
    assert dict(row["applicable_dimensions"])["rule"] == ("completeness",)
