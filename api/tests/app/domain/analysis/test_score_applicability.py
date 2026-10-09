"""SQL-shaped sparse score heads must not change immutable applicability."""

from copy import deepcopy

import pytest

from app.domain.analysis.score_summary import score_summary


def records(repeats=(3, 3, 3)):
    rows = []
    for case, count in zip("ABC", repeats, strict=True):
        for repeat in range(count):
            scored = case == "A" or (case == "B" and repeat == 0)
            rows.append(
                {
                    "family": "agent",
                    "dataset_version": "dataset",
                    "mode": "recorded",
                    "environment_version": "recorded",
                    "rubric": "rubric",
                    "config_id": "config",
                    "result_id": f"{case}{repeat}",
                    "case_id": case,
                    "execution_status": "succeeded",
                    "batch_id": "batch",
                    "evaluation_revision": 4,
                    "required_conditions": [
                        {"source": "human", "dimension_id": "quality", "minimum": 3}
                    ],
                    "source_sets": [
                        {
                            "source": "rule",
                            "required_dimensions": [],
                            "applicable_dimensions": ["quality"],
                        }
                    ]
                    + (
                        [
                            {
                                "source": "human",
                                "required_dimensions": ["quality"],
                                "applicable_dimensions": ["quality"],
                            }
                        ]
                        if scored
                        else []
                    ),
                    "scores": (
                        [
                            {
                                "source": "human",
                                "dimension": "quality",
                                "value": 4 if case == "A" else 0,
                                "status": "valid",
                                "invalidated": False,
                            }
                        ]
                        if scored
                        else []
                    ),
                }
            )
    return rows


@pytest.mark.parametrize(("repeats", "coverage"), [((3, 3, 3), 4 / 9), ((4, 2, 1), 0.5)])
def test_sparse_human_heads_keep_all_cases_and_case_equal_weights(repeats, coverage):
    rows = records(repeats)
    before = deepcopy(rows)
    result = score_summary(rows)
    assert len(result["series"]) == 1
    metrics = result["series"][0]["metrics"]
    assert metrics["human:quality:mean"]["value"] == 2
    assert metrics["human:quality:mean"]["sample_count"] == 2
    assert metrics["human:quality:mean"]["missing_count"] == 2
    assert metrics["human:quality:coverage"]["value"] == pytest.approx(coverage)
    assert metrics["human:quality:coverage"]["denominator"] == 3
    assert rows == before


def test_unreviewed_known_applicability_retains_null_mean_and_zero_coverage():
    rows = records()
    for row in rows:
        row["source_sets"] = row["source_sets"][:1]
        row["scores"] = []
    metrics = score_summary(rows)["series"][0]["metrics"]
    assert metrics["human:quality:mean"]["value"] is None
    assert metrics["human:quality:coverage"]["value"] == 0
    assert metrics["human:quality:coverage"]["denominator"] == 3
    assert metrics["confirmed_pass_rate"]["value"] == 0
    assert metrics["confirmed_pass_rate"]["missing_count"] == 3


@pytest.mark.parametrize("problem", ["missing", "conflicting", "new_rubric", "different_case"])
def test_unresolvable_applicability_fails_explicitly(problem):
    rows = records((1, 1, 1))
    if problem == "conflicting":
        rows[0]["source_sets"][1]["applicable_dimensions"] = []
    else:
        rows[0]["source_sets"] = []
        if problem == "new_rubric":
            rows[0]["rubric"] = "other"
        if problem == "different_case":
            rows[0]["case_id"] = "other"
    with pytest.raises(ValueError, match="analysis_applicability_unavailable"):
        score_summary(rows)


def test_same_immutable_case_fallback_does_not_manufacture_completion():
    rows = records()
    rows[1]["source_sets"] = []
    rows[1]["scores"] = []
    summary = score_summary(rows)
    metrics = summary["series"][0]["metrics"]
    assert metrics["human:quality:coverage"]["value"] == pytest.approx(1 / 3)
    assert metrics["confirmed_pass_rate"]["value"] == pytest.approx(2 / 9)
    assert len(rows[1]["source_sets"]) == 0
    with pytest.raises(ValueError, match="analysis_applicability_unavailable"):
        score_summary([rows[1]])  # revoked evidence is not available to this read


def test_late_review_changes_values_at_new_cut_without_changing_stratum():
    original = records()
    later = deepcopy(original)
    for row in later:
        row["evaluation_revision"] = 5
    later[-1]["source_sets"].append(deepcopy(later[0]["source_sets"][1]))
    later[-1]["scores"] = deepcopy(later[0]["scores"])
    before, after = score_summary(original), score_summary(later)
    assert before["series"][0]["identity"] == after["series"][0]["identity"]
    assert (
        before["series"][0]["applicable_dimensions"] == after["series"][0]["applicable_dimensions"]
    )
    assert before["series"][0]["metrics"]["human:quality:coverage"]["value"] == pytest.approx(4 / 9)
    assert after["series"][0]["metrics"]["human:quality:coverage"]["value"] == pytest.approx(5 / 9)
    assert before["evaluation_cuts"] == [{"batch_id": "batch", "evaluation_revision": 4}]
    assert after["evaluation_cuts"] == [{"batch_id": "batch", "evaluation_revision": 5}]


@pytest.mark.parametrize(
    ("status", "invalidated", "execution", "missing", "excluded"),
    [
        ("failed", False, "succeeded", 1, 0),
        ("timeout", False, "succeeded", 1, 0),
        ("not_evaluable", False, "succeeded", 1, 0),
        ("valid", True, "succeeded", 0, 1),
        ("valid", False, "failed", 0, 1),
    ],
)
def test_nonvalid_heads_remain_missing_or_excluded(
    status, invalidated, execution, missing, excluded
):
    row = records((1, 0, 0))[0]
    row["scores"][0].update(status=status, invalidated=invalidated)
    row["execution_status"] = execution
    metrics = score_summary([row])["series"][0]["metrics"]
    assert metrics["human:quality:mean"]["value"] is None
    assert metrics["human:quality:mean"]["missing_count"] == missing
    assert metrics["human:quality:mean"]["excluded_count"] == excluded
    assert metrics["confirmed_pass_rate"]["value"] == 0


def test_inapplicable_and_unknown_are_distinct_and_empty_capture_is_valid():
    row = records((1, 0, 0))[0]
    row["scores"] = []
    row["source_sets"] = [
        {"source": "rule", "required_dimensions": [], "applicable_dimensions": []}
    ]
    metrics = score_summary([row])["series"][0]["metrics"]
    assert "human:quality:mean" not in metrics
    assert metrics["confirmed_pass_rate"]["value"] == 1
    assert score_summary([])["series"] == []


def test_fallback_never_crosses_dataset_or_rubric_identity():
    for field in ("dataset_version", "rubric"):
        rows = records((2, 0, 0))
        rows[1][field] = "different"
        rows[1]["source_sets"] = []
        with pytest.raises(ValueError, match="analysis_applicability_unavailable"):
            score_summary(rows)


def test_conflicting_repeat_metadata_is_not_union_or_intersection():
    rows = records((2, 0, 0))
    for item in rows[1]["source_sets"]:
        item["applicable_dimensions"] = ["different"]
    with pytest.raises(ValueError, match="analysis_applicability_unavailable"):
        score_summary(rows)
