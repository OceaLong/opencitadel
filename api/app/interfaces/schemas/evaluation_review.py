"""Public typed review commands with explicit revisions and added judge budget."""

from uuid import UUID

from pydantic import Field

from app.domain.evaluation.dataset import ImmutableModel
from app.domain.evaluation.judge_protocol import RescoreRequest
from app.domain.evaluation.review import HumanReview


class AppendHumanReview(HumanReview):
    request_id: str = Field(min_length=1, max_length=255)
    expected_revision: int = Field(ge=0, strict=True)


class RescoreCommand(RescoreRequest):
    request_id: str = Field(min_length=1, max_length=255)


class CancelJudgeCommand(ImmutableModel):
    request_id: str = Field(min_length=1, max_length=255)
    judge_run_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    expected_result_revision: int = Field(ge=1, strict=True)
