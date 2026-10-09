"""Source-separated score-only read projection, with explicit independent usage cut."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.domain.evaluation.dataset import ImmutableModel


class UsageSummary(ImmutableModel):
    calls: int = Field(ge=0)
    token_known: int = Field(ge=0)
    money_known: int = Field(ge=0)
    tokens: int | None
    money: str | None
    unresolved: int = Field(ge=0)


class SummaryAttempt(ImmutableModel):
    attempt: int
    run_id: UUID
    run_revision: int
    status: str


class SummaryRow(ImmutableModel):
    id: UUID
    case_id: UUID
    case_label: str
    config_id: UUID
    config_label: str
    repetition: int
    attempt: int
    run_id: UUID | None
    run_revision: int | None
    result_revision: int
    attempts: tuple[SummaryAttempt, ...] = ()
    score_run_id: UUID | None
    score_run_revision: int | None
    score_result_revision: int | None
    execution_status: str
    scoring_status: str
    value: bool | int | None
    invalidated: bool
    subject_usage: UsageSummary | None
    judge_usage: UsageSummary | None


class AllocationSummary(ImmutableModel):
    kind: Literal["original", "additional"]
    token_budget: int
    money_budget: str | None
    authorizer: str
    intent_id: UUID | None


class EvaluationSnapshot(ImmutableModel):
    id: UUID
    batch_id: UUID
    captured_at: datetime
    usage_watermark: datetime
    expires_at: datetime
    evaluation_revision: int
    source: Literal["rule", "human", "model"]
    dimension: str
    rubric_id: UUID
    rows: tuple[SummaryRow, ...] = Field(max_length=5000)
    allocations: tuple[AllocationSummary, ...]


class SummaryPoint(ImmutableModel):
    excluded: bool
    result_id: UUID
    case_id: UUID
    config_id: UUID
    value: float | None
    cost_usd: str | None


class SeriesMetadata(ImmutableModel):
    numerator: None = None
    denominator: None = None
    sample_count: int
    missing_count: int
    excluded_count: int
    grain: Literal["case_config", "case_result"]
    timezone: Literal["UTC"] = "UTC"
    watermark: datetime
    metric_version: Literal["evaluation-series-v1"] = "evaluation-series-v1"


class SummaryPage(ImmutableModel):
    snapshot_id: UUID
    batch_id: UUID
    captured_at: datetime
    usage_watermark: datetime
    expires_at: datetime
    evaluation_revision: int
    source: Literal["rule", "human", "model"]
    dimension: str
    rubric_id: UUID
    items: tuple[SummaryRow, ...]
    selected_result: SummaryRow | None
    next_cursor: str | None
    points: tuple[SummaryPoint, ...]
    allocations: tuple[AllocationSummary, ...]
    distribution_metadata: SeriesMetadata
    quality_cost_metadata: SeriesMetadata
    scoring_counts: dict[str, int]
    subject_usage: UsageSummary | None
    judge_usage: UsageSummary | None


class BatchListItem(ImmutableModel):
    id: UUID
    name: str
    suite_version: UUID
    status: str
    revision: int
    evaluation_revision: int
    created_at: datetime


class BatchListPage(ImmutableModel):
    items: tuple[BatchListItem, ...]
    next_cursor: str | None


class BatchRevisionEvent(ImmutableModel):
    cursor: str
    revision: int
    kind: str
