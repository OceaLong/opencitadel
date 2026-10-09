"""Scoring definitions and dataset-conditional reference requirements (not scoring)."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domain.evaluation.configuration import PositiveInt
from app.domain.evaluation.dataset import ImmutableModel, validate_publication


class RubricDimension(ImmutableModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    anchors: tuple[str, str, str, str, str]
    evidence_required: bool = False

    @model_validator(mode="after")
    def descriptions(self):
        if any(not anchor.strip() for anchor in self.anchors):
            raise ValueError("all_five_anchors_required")
        return self


def default_dimensions():
    return (
        RubricDimension(
            id="correctness",
            name="Correctness",
            anchors=(
                "Contradicts the available reference.",
                "Major errors undermine the answer.",
                "Partly correct with material errors.",
                "Correct with minor errors.",
                "Fully correct against the available reference.",
            ),
        ),
        RubricDimension(
            id="completeness",
            name="Completeness",
            anchors=(
                "Does not address the task.",
                "Addresses few essential requirements.",
                "Addresses some but misses material requirements.",
                "Addresses all major requirements with minor omissions.",
                "Addresses all applicable requirements.",
            ),
        ),
        RubricDimension(
            id="source_support",
            name="Source support",
            evidence_required=True,
            anchors=(
                "No supporting evidence.",
                "Most claims lack support.",
                "Some material claims are supported.",
                "Most claims have relevant supporting sources.",
                "All material claims are supported by the supplied sources; this does not establish external truth.",
            ),
        ),
    )


class RequiredCondition(ImmutableModel):
    dimension_id: str
    minimum: int = Field(ge=0, le=4, strict=True)
    source: Literal["model", "human"]


class RubricDefinition(ImmutableModel):
    dimensions: tuple[RubricDimension, ...] = Field(
        default_factory=default_dimensions, min_length=1, max_length=20
    )
    reference_policy: Literal["optional", "required", "required_when_applicable"] = "optional"
    reference_dimensions: tuple[str, ...] = ()
    required_conditions: tuple[RequiredCondition, ...] = ()
    judge_config_version: UUID

    @model_validator(mode="after")
    def validate_dimensions(self):
        ids = {d.id for d in self.dimensions}
        if (
            len(ids) != len(self.dimensions)
            or not set(self.reference_dimensions) <= ids
            or any(c.dimension_id not in ids for c in self.required_conditions)
        ):
            raise ValueError("invalid_dimensions")
        if self.reference_policy == "required_when_applicable" and not self.reference_dimensions:
            raise ValueError("reference_dimensions_required")
        return self


class RubricVersion(RubricDefinition):
    id: UUID
    entity_id: UUID
    revision: PositiveInt
    name: str
    fingerprint: str


def validate_references(rubric, cases):
    validate_publication(cases)
    ids = {d.id for d in rubric.dimensions}
    for case in cases:
        applicable = set(case.applicable_dimensions) or ids
        if not applicable <= ids:
            raise ValueError("unknown_applicable_dimension")
        required = rubric.reference_policy == "required" or (
            rubric.reference_policy == "required_when_applicable"
            and bool(applicable & set(rubric.reference_dimensions))
        )
        if required and not (case.reference_answer and case.reference_confirmed):
            raise ValueError("reference_required")
