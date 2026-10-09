import pytest

from app.domain.analysis.charts import chart_metrics


def run(identity, status, duration):
    return {
        "run_id": identity,
        "status": status,
        "duration_ms": duration,
        "family": "agent",
        "purpose": "subject",
        "execution_mode": "recorded",
        "configuration_revision": None,
    }


def test_latency_uses_cohort_samples_not_mean_of_bucket_percentiles():
    result = chart_metrics(
        {
            "runs": [run("a", "completed", 1), run("b", "failed", 3), run("c", "completed", 100)],
            "tools": [],
        }
    )
    assert result["latency"]["p50"]["value"] == 3
    assert result["latency"]["p95"]["value"] == pytest.approx(90.3)
    assert result["latency"]["samples"] == [
        {"run_id": "a", "duration_ms": 1},
        {"run_id": "b", "duration_ms": 3},
        {"run_id": "c", "duration_ms": 100},
    ]


def test_latency_boundaries_overflow_and_missing_are_explicit():
    rows = [
        run("a", "completed", 100),
        run("b", "failed", 600001),
        run("c", "failed", None),
        run("d", "cancelled", None),
    ]
    result = chart_metrics({"runs": rows, "tools": []})["latency"]
    assert result["bin_counts"][:2] == [0, 1]
    assert result["overflow"]["count"] == 1
    assert result["overflow"]["maximum_ms"] == 600001
    assert result["p50"]["sample_count"] == 2
    assert result["p50"]["missing_count"] == 1
    assert result["p50"]["excluded_count"] == 1


def test_nineteen_exact_samples_twenty_histogram_only():
    for n in (19, 20):
        result = chart_metrics(
            {"runs": [run(str(i), "completed", i) for i in range(n)], "tools": []}
        )["latency"]
        assert len(result["samples"]) == (19 if n == 19 else 0)
        assert sum(result["bin_counts"]) + result["overflow"]["count"] == n


def test_tool_error_union_is_not_sum_of_execution_and_business_failures():
    fact = {
        "tool_name": "tool",
        "terminal": 4,
        "errors": 2,
        "execution_errors": 2,
        "business_errors": 1,
        "excluded": 3,
        "unknown": 1,
        "deferred": 1,
        "cancelled": 1,
    }
    row = chart_metrics({"runs": [], "tools": [fact]})["tools"]["items"][0]
    assert row["error_rate"]["value"] == 0.5
    assert row["error_rate"]["numerator"] == 2
    assert row["error_rate"]["denominator"] == 4
    assert row["error_rate"]["excluded_count"] == 3


def test_historical_missing_tool_facts_are_unavailable_not_empty():
    assert (
        chart_metrics({"runs": [], "tools": None})["tools"]["availability"]
        == "retained_data_unavailable"
    )
