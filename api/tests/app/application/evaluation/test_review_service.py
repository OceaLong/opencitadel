import pytest
from pydantic import ValidationError


def test_missing_dimension_keeps_review_pending():
    from app.application.evaluation.review_service import review_complete

    assert not review_complete({"correctness", "grounding"}, {"correctness"})
    assert review_complete({"correctness"}, {"correctness"})


@pytest.mark.parametrize("value", [True, -1, 5, 1.5, "3"])
def test_human_score_is_strict_integer(value):
    from app.domain.evaluation.review import HumanScore

    with pytest.raises(ValidationError):
        HumanScore(dimension="correctness", value=value)
