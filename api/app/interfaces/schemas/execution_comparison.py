"""Only selection identities enter comparison writes; facts and authors are server owned."""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.dto.execution_view import StepView
from app.interfaces.schemas.execution_analysis import (
    AnalysisMetrics,
    AnalysisRun,
    ComparisonContext,
)


class StrictComparisonModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ComparisonCreate(StrictComparisonModel):
    request_id: str = Field(min_length=1, max_length=128)
    mode: Literal["explicit", "all_matching"] = "explicit"
    run_ids: list[UUID] = Field(default_factory=list, max_length=100000)
    excluded_run_ids: list[UUID] = Field(default_factory=list, max_length=100000)
    detail_run_ids: list[UUID] = Field(default_factory=list, max_length=5)
    baseline_configuration: UUID | None = None
    filters: dict[str, str | list[str]] = Field(default_factory=dict)
    grain: Literal["hour", "day"] = "day"
    timezone: str = "UTC"


class ComparisonRefresh(ComparisonCreate):
    expected_revision: int = Field(ge=1)


class AlignmentEdit(StrictComparisonModel):
    left_run_id: UUID
    right_run_id: UUID
    left_step_id: str = Field(min_length=1, max_length=255)
    right_step_id: str = Field(min_length=1, max_length=255)
    left_attempt_id: str | None = Field(default=None, max_length=255)
    right_attempt_id: str | None = Field(default=None, max_length=255)
    action: Literal["confirm", "unpair"]


class ComparisonAlignment(StrictComparisonModel):
    revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)
    edits: list[AlignmentEdit] = Field(min_length=1, max_length=100)


class ArtifactSelection(StrictComparisonModel):
    run_id: UUID
    step_id: str = Field(min_length=1, max_length=255)
    artifact_id: str = Field(min_length=1, max_length=255)
    version: int = Field(ge=1)


class ComparisonArtifactDiff(StrictComparisonModel):
    revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)
    left: ArtifactSelection
    right: ArtifactSelection
    format: Literal["text", "json"] = "text"


class RetainedSteps(BaseModel):
    steps: list[StepView] = Field(max_length=10000)


class ComparisonDetail(BaseModel):
    run_id: str
    availability: Literal["available", "retained_data_unavailable"]
    body: RetainedSteps | None


class ComparisonMember(AnalysisRun):
    cut: str


class AlignmentLocator(BaseModel):
    run_id: str
    step_id: str
    attempt_id: str | None
    cut: str
    kind: str


class AlignmentSuggestion(BaseModel):
    left: AlignmentLocator
    right: AlignmentLocator | None
    status: str
    provenance: str
    reason: str
    algorithm_version: str
    run_pair: list[str]
    revision: int | None = None
    supersedes: int | None = None
    author: str | None = None
    created_at: str | None = None


class AlignmentRecord(BaseModel):
    revision: int
    supersedes: int
    author: str
    created_at: str
    edit: AlignmentEdit


class ComparisonEnvelope(BaseModel):
    comparison_id: UUID
    revision: Annotated[int, Field(ge=1)]
    alignment_revision: Annotated[int, Field(ge=0)]
    accepted_alignment_revision: Annotated[int, Field(ge=0)] | None = None
    captured_at: str
    timezone: str
    metric_version: str
    coverage_changed: bool
    member_count: Annotated[int, Field(ge=0, le=100000)]
    baseline_configuration: str | None
    members: list[ComparisonMember]
    context: ComparisonContext | None = None
    next_cursor: str | None
    details: list[ComparisonDetail]
    alignments: list[AlignmentRecord]
    suggestions: list[AlignmentSuggestion]
    metrics: AnalysisMetrics


class ComparisonDiffQueued(BaseModel):
    status: Literal["queued"]
    job_id: UUID


class ComparisonDiffPage(BaseModel):
    job_id: UUID
    status: Literal["queued", "running", "complete", "partial", "failed"]
    result: dict[str, Any] | None
    encoding: Literal["json-utf8"]
    content: str | None
    next_cursor: str | None
