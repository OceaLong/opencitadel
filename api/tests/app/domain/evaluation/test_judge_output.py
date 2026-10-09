import json

import pytest

from app.domain.evaluation.judge_output import parse_judge_output, validate_score


def output(**updates):
    return json.dumps(
        {
            "status": "complete",
            "dimensions": [
                {"name": "correctness", "score": 4, "reason": "Matches reference", "evidence": []}
            ],
            "unavailable_reason": None,
            **updates,
        }
    )


def test_judge_score_rejects_boolean_and_out_of_range():
    for value in (True, False, 5, -1, 2.5, 4.0, "4", None):
        with pytest.raises(ValueError, match=r".+"):
            validate_score(value)
    assert validate_score(4) == 4


def test_strict_json_and_dimensions():
    result = parse_judge_output(output())
    assert result.dimensions[0].score == 4
    for text in (
        "```json\n" + output() + "\n```",
        output() + " trailing",
        '{"deep":' + "[" * 2000 + "0" + "]" * 2000 + "}",
        output(dimensions=[{"name": "x", "score": 4, "reason": "   ", "evidence": []}]),
        '{"status":"complete","status":"complete","dimensions":[],"unavailable_reason":null}',
        output(extra=True),
        output(dimensions=[]),
        output(dimensions=[{"name": "x", "score": True, "reason": "a", "evidence": []}]),
        output(dimensions=[{"name": "x", "score": 4, "reason": "a", "evidence": []}] * 2),
        output(dimensions=[{"name": "x", "score": float("nan"), "reason": "a", "evidence": []}]),
    ):
        with pytest.raises(ValueError, match=r".+"):
            parse_judge_output(text)


def test_unavailable_is_explicit_null_never_zero():
    result = parse_judge_output(
        output(
            status="not_evaluable",
            dimensions=[
                {
                    "name": "source_support",
                    "score": None,
                    "reason": "No authorized evidence",
                    "evidence": [],
                }
            ],
            unavailable_reason="missing_evidence",
        )
    )
    assert result.dimensions[0].score is None
    with pytest.raises(ValueError, match=r".+"):
        parse_judge_output(output(status="not_evaluable", unavailable_reason="missing_evidence"))


def test_contextual_evidence_and_exact_applicable_dimension_validation():
    from app.domain.evaluation.judge_protocol import validate_output

    material = {
        "rubric": [{"id": "correctness", "evidence_required": False}],
        "evidence": {},
        "unavailable": {},
    }
    assert validate_output(output(), material).dimensions[0].score == 4
    for updated in (
        {**material, "rubric": [{"id": "other", "evidence_required": False}]},
        {**material, "unavailable": {"correctness": "missing reference"}},
        {**material, "rubric": [{"id": "correctness", "evidence_required": True}]},
    ):
        with pytest.raises(ValueError, match=r".+"):
            validate_output(output(), updated)
    with pytest.raises(ValueError, match=r".+"):
        validate_output(
            output(
                dimensions=[
                    {
                        "name": "correctness",
                        "score": 4,
                        "reason": "OK",
                        "evidence": ["https://invented"],
                    }
                ]
            ),
            material,
        )
