from uuid import uuid4

import pytest

from app.domain.evaluation.scoring import ScoreValue, required_rule_case, rule_settlement


def test_missing_is_not_zero_and_sources_have_distinct_ranges():
    for source in ("rule", "model", "human"):
        with pytest.raises(ValueError, match="missing_score_must_be_null"):
            ScoreValue(source=source, status="not_evaluable", value=0)
    with pytest.raises(ValueError, match="dimension_score_out_of_range"):
        ScoreValue(source="human", status="valid", value=True)
    with pytest.raises(ValueError, match="rule_score_must_be_boolean"):
        ScoreValue(source="rule", status="valid", value=0)
    for source in ("model", "human"):
        assert ScoreValue(source=source, status="valid", value=0).value == 0
        with pytest.raises(ValueError, match="dimension_score_out_of_range"):
            ScoreValue(source=source, status="valid", value=5)


def test_multiple_failed_rules_count_one_case_not_errors():
    failed = ScoreValue(status="valid", value=False, rubric_revision=uuid4())
    assert required_rule_case([failed, failed]) == "failed"
    assert rule_settlement([failed, failed]) == "complete"
    missing = ScoreValue(status="not_evaluable", value=None)
    assert required_rule_case([failed, missing]) == "missing"
    assert required_rule_case([]) == "excluded"


def test_rule_case_metric_denominator_excludes_execution_failure_and_missing():
    from app.domain.evaluation.scoring import required_rule_summary

    passed = ScoreValue(status="valid", value=True)
    failed = ScoreValue(status="valid", value=False)
    missing = ScoreValue(status="not_evaluable", value=None)
    summary = required_rule_summary(
        [
            ("succeeded", [passed]),
            ("succeeded", [failed, failed]),
            ("succeeded", [missing]),
            ("failed", []),
            ("succeeded", []),
        ],
        watermark="evaluation:7",
        timezone="UTC",
    )
    assert summary.numerator == 1
    assert summary.denominator == 2
    assert summary.missing_count == 1
    assert summary.excluded_count == 2
    assert summary.execution_failed_count == 1
    assert summary.grain == "case"
    assert summary.watermark == "evaluation:7"


def test_recording_marker_requires_typed_consumption_coverage():
    from app.domain.evaluation.scoring import RecordingEvidence

    with pytest.raises(ValueError, match="invalid_recording_coverage"):
        RecordingEvidence(
            version_id=uuid4(),
            revision=1,
            total=0,
            consumed=1,
            mismatches=0,
            simulated_activity_ids=(),
        )


@pytest.mark.parametrize(("source", "value"), [("rule", "true"), ("model", 3.0), ("human", "4")])
def test_scores_do_not_coerce_wire_values(source, value):
    with pytest.raises(ValueError, match="Input should be"):
        ScoreValue(source=source, status="valid", value=value)
