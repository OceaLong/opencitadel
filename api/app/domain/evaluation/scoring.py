"""Source-separated immutable scores; missing values never become numeric zero."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, model_validator

from app.domain.evaluation.dataset import ImmutableModel
from app.domain.models.resource_pin import ResourceIdentity


class ScoringProjectionAdvanced(ValueError):
    """A still-eligible subject has a newer completed Run projection cut."""


class RecordingEvidence(ImmutableModel):
    """Trusted binding, immutable slot policy and accepted F06 consumption corroboration."""

    version_id: UUID
    revision: int = Field(ge=1)
    total: int = Field(ge=0)
    consumed: int = Field(ge=0)
    mismatches: int = Field(ge=0)
    simulated_activity_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def consistent(self):
        if (
            self.consumed > self.total
            or len(set(self.simulated_activity_ids)) != len(self.simulated_activity_ids)
            or len(self.simulated_activity_ids) > self.consumed
        ):
            raise ValueError("invalid_recording_coverage")
        return self

    @property
    def unused(self):
        return self.total - self.consumed

    @property
    def quality_eligible(self):
        return self.mismatches == 0


class ScoreValue(ImmutableModel):
    dimension: str = Field(default="rule", min_length=1, max_length=255)
    source: Literal["rule", "model", "human"] = "rule"
    rubric_revision: UUID | None = None
    value: StrictBool | StrictInt | None
    reason: str = Field(default="", max_length=2000)
    evidence: tuple[ResourceIdentity, ...] = ()
    recording: RecordingEvidence | None = None
    status: Literal["valid", "not_evaluable", "error"]

    @model_validator(mode="after")
    def validity(self):
        if self.status != "valid":
            if self.value is not None:
                raise ValueError("missing_score_must_be_null")
        elif self.source == "rule":
            if type(self.value) is not bool:
                raise ValueError("rule_score_must_be_boolean")
        elif type(self.value) is not int or not 0 <= self.value <= 4:
            raise ValueError("dimension_score_out_of_range")
        return self


class ScoreRevision(ImmutableModel):
    id: UUID
    source_set_id: UUID | None = None
    result_id: UUID
    result_revision: int = Field(ge=1)
    run_id: UUID
    judge_run_id: UUID | None = None
    run_revision: int = Field(ge=1)
    evaluation_revision: int = Field(ge=1)
    score: ScoreValue
    supersedes_id: UUID | None = None
    author: str = Field(min_length=1)
    timestamp: datetime

    @model_validator(mode="after")
    def bound(self):
        if self.score.rubric_revision is None or self.timestamp.tzinfo is None:
            raise ValueError("score_revision_requires_rubric_and_aware_time")
        return self


def rule_settlement(scores):
    """Settlement concerns evaluation completion; business failure stays a valid score."""
    if not scores:
        return "not_required"
    if any(score.status == "error" for score in scores):
        return "failed"
    if any(score.status == "not_evaluable" for score in scores):
        return "skipped"
    return "complete"


def required_rule_case(scores):
    """One required-rule case: valid completed cases form the pass-rate denominator."""
    if not scores:
        return "excluded"
    if any(score.status != "valid" for score in scores):
        return "missing"
    return "passed" if all(score.value is True for score in scores) else "failed"


class RequiredRuleSummary(ImmutableModel):
    numerator: int
    denominator: int
    missing_count: int
    excluded_count: int
    execution_failed_count: int
    grain: Literal["case"] = "case"
    timezone: str
    watermark: str
    metric_version: Literal["required-rule-cases-v1"] = "required-rule-cases-v1"


def required_rule_summary(cases, *, watermark, timezone):
    """Each tuple is one case with its required rules only; optional rules never inflate coverage."""
    from zoneinfo import ZoneInfo

    ZoneInfo(timezone)
    numerator = denominator = missing = excluded = execution_failed = 0
    for execution, scores in cases:
        if execution != "succeeded":
            excluded += 1
            execution_failed += execution in {"failed", "mismatch"}
            continue
        outcome = required_rule_case(scores)
        if outcome in {"passed", "failed"}:
            denominator += 1
            numerator += outcome == "passed"
        elif outcome == "missing":
            missing += 1
        else:
            excluded += 1
    return RequiredRuleSummary(
        numerator=numerator,
        denominator=denominator,
        missing_count=missing,
        excluded_count=excluded,
        execution_failed_count=execution_failed,
        timezone=timezone,
        watermark=watermark,
    )
