import pytest


def test_comparability_partitions_family_versions_source_and_applicability():
    from app.domain.analysis.comparability import comparable_key

    row = {
        "family": "ask",
        "dataset_version": "d1",
        "mode": "recorded",
        "environment_version": "e1",
        "rubric": "r1",
        "metric_version": "v1",
        "applicable_dimensions": ["b", "a"],
        "source": "human",
        "dimension": "a",
    }
    key = comparable_key(row)
    assert key == comparable_key(
        {**row, "applicable_dimensions": ["a", "b"], "configuration": "other"}
    )
    for field in [
        "family",
        "dataset_version",
        "mode",
        "environment_version",
        "rubric",
        "metric_version",
        "source",
        "dimension",
    ]:
        assert key != comparable_key({**row, field: "changed"})
    with pytest.raises(ValueError, match="missing comparability identity"):
        comparable_key({})


def test_paired_case_means_use_intersection_and_repeat_average():
    from app.domain.analysis.comparability import paired_case_comparison

    result = paired_case_comparison(
        [("a", 4), ("a", 2), ("b", 0), ("only_left", 4)], [("a", 1), ("b", 0)]
    )
    assert result.mean_left == 1.5
    assert result.mean_right == 0.5
    assert result.delta == 1
    assert result.case_count == 2
    assert result.confidence_interval is None
    assert result.relative_delta == 2
    assert paired_case_comparison([("x", 1)], [("y", 1)]).delta is None
    assert paired_case_comparison([("x", 1)], [("x", 0)]).relative_delta is None


def test_bootstrap_resamples_paired_cases_and_is_order_independent():
    from app.domain.analysis.comparability import paired_case_comparison

    left = [(str(i), i % 5) for i in range(20)]
    right = [(str(i), i % 5 - 1) for i in range(20)]
    result = paired_case_comparison(left, right)
    assert result.confidence_interval == (1, 1)
    assert result.bootstrap_samples == 2000
    assert result == paired_case_comparison(left[::-1], right[::-1])
    assert paired_case_comparison(left[:19], right[:19]).confidence_interval is None


def test_many_configurations_require_selection_without_dropping_descriptive_series():
    from app.domain.analysis.score_summary import score_summary

    records = []
    for index in range(6):
        config = str(index)
        records.append(
            {
                "family": "agent",
                "dataset_version": "d",
                "mode": "recorded",
                "environment_version": "r",
                "rubric": "rubric",
                "evaluation_revision": 1,
                "result_id": config,
                "case_id": "case",
                "config_id": config,
                "execution_status": "succeeded",
                "required_conditions": [],
                "source_sets": [
                    {
                        "source": "model",
                        "required_dimensions": [],
                        "applicable_dimensions": ["quality"],
                        "source_set_id": config,
                    }
                ],
                "scores": [
                    {
                        "source": "model",
                        "dimension": "quality",
                        "value": index % 5,
                        "status": "valid",
                        "invalidated": False,
                        "source_set_id": config,
                    }
                ],
            }
        )
    output = score_summary(records)
    assert len(output["series"]) == 6
    assert output["comparisons"] == []
    assert output["comparison_selection_required"]
    chosen = score_summary(records, selection=("0", "1"))
    assert len(chosen["series"]) == 6
    assert len(chosen["comparisons"]) == 1


def test_explicit_comparison_selection_rejects_incompatible_fixed_versions():
    from app.domain.analysis.score_summary import score_summary

    records = [
        {
            "family": "agent",
            "dataset_version": dataset,
            "mode": "recorded",
            "environment_version": "recorded",
            "rubric": "r",
            "result_id": config,
            "case_id": "case",
            "config_id": config,
            "execution_status": "succeeded",
            "required_conditions": [],
            "source_sets": [
                {"source": "rule", "required_dimensions": [], "applicable_dimensions": []}
            ],
            "scores": [],
        }
        for config, dataset in [("a", "d1"), ("b", "d2")]
    ]
    with pytest.raises(ValueError, match="analysis_comparison_selection_unavailable"):
        score_summary(records, selection=("a", "b"))
