"""Public review commands carry explicit revisions, never authors or private judge input."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domain.evaluation.dataset import ImmutableModel
from app.domain.evaluation.judge_protocol import JudgeSourceInvalidation, RescoreRequest
from app.domain.evaluation.rubric import RubricVersion
from app.domain.evaluation.scoring import ScoreRevision
from app.domain.models.resource_pin import ResourceIdentity


class HumanScore(ImmutableModel):
    dimension: str = Field(min_length=1, max_length=100)
    value: int | None = Field(default=None, strict=True, ge=0, le=4)
    status: Literal["valid", "not_evaluable", "error"] = "valid"
    reason: str = Field(default="", max_length=2000)
    evidence: tuple[ResourceIdentity, ...] = Field(default=(), max_length=100)
    supersedes_id: UUID | None = None

    @model_validator(mode="after")
    def validity(self):
        if (self.status == "valid") != (self.value is not None):
            raise ValueError("invalid_human_value")
        return self


class HumanReview(ImmutableModel):
    rubric_version: UUID
    expected_result_revision: int = Field(ge=1, strict=True)
    scores: tuple[HumanScore, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique(self):
        if len({s.dimension for s in self.scores}) != len(self.scores):
            raise ValueError("duplicate_dimension")
        return self


class ReviewReceipt(ImmutableModel):
    id: UUID
    result_id: UUID
    kind: Literal["human", "rescore", "cancel"]
    status: Literal[
        "accepted",
        "queued",
        "processing",
        "submitted",
        "cancelling",
        "failed",
        "completed",
        "cancelled",
    ]
    evaluation_revision: int = Field(ge=0)
    result_revision: int = Field(ge=1)
    review_status: Literal["not_required", "pending", "complete"]
    judge_run_id: UUID | None = None
    error: str | None = None


class ReviewItem(ImmutableModel):
    result_id: UUID
    batch_id: UUID
    run_id: UUID
    rubric_version: UUID
    result_revision: int
    evaluation_revision: int
    execution_status: str
    scoring_status: str
    review_status: Literal["not_required", "pending", "complete"]
    required_dimensions: tuple[str, ...]
    received_dimensions: tuple[str, ...]


class ReviewPage(ImmutableModel):
    items: tuple[ReviewItem, ...]
    next_cursor: str | None = None


__all__ = [
    "HumanReview",
    "HumanScore",
    "RescoreRequest",
    "ReviewItem",
    "ReviewPage",
    "ReviewReceipt",
]


class ScoreHistoryPage(ImmutableModel):
    invalidations: tuple[JudgeSourceInvalidation, ...] = ()
    items: tuple[ScoreRevision, ...]
    evaluation_revision: int
    next_cursor: str | None = None


def case_review_requirements(dataset, rubric):
    """Immutable original-rubric requirements, intersected with each fixed case."""
    mandatory = {c.dimension_id for c in rubric.required_conditions if c.source == "human"}
    dimensions = {d.id for d in rubric.dimensions}
    return {
        str(c.id): bool(mandatory & (set(c.applicable_dimensions) or dimensions))
        for c in dataset.cases
    }


class CurrentHumanHead(HumanScore):
    id: UUID
    evaluation_revision: int


class CurrentReviewContext(ImmutableModel):
    result_id: UUID
    batch_id: UUID
    result_revision: int
    evaluation_revision: int
    rubric: RubricVersion
    applicable_dimensions: tuple[str, ...]
    human_heads: tuple[CurrentHumanHead, ...]
