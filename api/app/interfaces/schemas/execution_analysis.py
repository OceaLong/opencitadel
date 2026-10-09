"""Strict public analysis query/preferences. Identity and scope are never inputs."""

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.evaluation.summary import SeriesMetadata, SummaryPoint, SummaryRow, UsageSummary


class ChartMetric(BaseModel):
    value: float | int | None
    unit: str
    numerator: float | int | None = None
    denominator: int | None = None
    sample_count: int
    missing_count: int
    excluded_count: int


class LatencySample(BaseModel):
    run_id: str
    duration_ms: float


class LatencyOverflow(BaseModel):
    lower_ms: int
    count: int
    maximum_ms: float | None


class LatencyChart(BaseModel):
    scheme: Literal["execution-latency-ms-v1"]
    edges_ms: list[int]
    edge_convention: Literal["lower_inclusive_upper_exclusive"]
    bin_counts: list[int]
    overflow: LatencyOverflow
    p50: ChartMetric
    p95: ChartMetric
    samples: list[LatencySample]


class LatencyGroup(LatencyChart):
    group: dict[str, str | None]


class ToolChartRow(BaseModel):
    tool_name: str | None
    error_rate: ChartMetric
    terminal: int
    errors: int
    execution_errors: int
    business_errors: int
    excluded: int
    unknown: int
    deferred: int
    cancelled: int


class ToolChart(BaseModel):
    availability: Literal["available", "retained_data_unavailable"]
    items: list[ToolChartRow]


class AnalysisCharts(BaseModel):
    latency: LatencyChart
    latency_groups: list[LatencyGroup]
    tools: ToolChart


class AnalysisGroup(BaseModel):
    family: str | None
    purpose: str | None
    execution_mode: str | None
    configuration_revision: str | None
    bucket: str


class AnalysisSeries(BaseModel):
    group: AnalysisGroup
    metrics: dict[str, ChartMetric]


class ScoreGroup(BaseModel):
    identity: tuple[str, str, str, str, str]
    applicable_dimensions: list[tuple[str, list[str]]]
    configuration: str
    metrics: dict[str, ChartMetric]


class ScoreComparison(BaseModel):
    identity: list[str | list[str]]
    left: str
    right: str
    mean_left: float | None
    mean_right: float | None
    delta: float | None
    relative_delta: float | None
    case_count: int
    confidence_interval: tuple[float, float] | None
    bootstrap_samples: int
    seed: int
    interpretation: str


class EvaluationCut(BaseModel):
    batch_id: str
    evaluation_revision: int


class ScoreSelectionRequired(BaseModel):
    identity: list[str | list[str]]
    status: Literal["comparison_selection_required"]


class AnalysisScores(BaseModel):
    series: list[ScoreGroup] = Field(default_factory=list)
    comparisons: list[ScoreComparison] = Field(default_factory=list)
    comparison_selection_required: list[ScoreSelectionRequired] = Field(default_factory=list)
    evaluation_cuts: list[EvaluationCut] = Field(default_factory=list)
    selection_status: Literal["available", "unavailable"] | None = None


class AnalysisEvaluationSeries(BaseModel):
    identity: list[str | list[str]]
    batch_id: str
    evaluation_revision: int
    source: Literal["rule", "human", "model"]
    dimension: str
    rubric_id: str
    captured_at: str
    usage_watermark: str
    cost_basis: Literal["case_result_subject_plus_judge"]
    rows: list[SummaryRow]
    points: list[SummaryPoint]
    distribution_metadata: SeriesMetadata
    quality_cost_metadata: SeriesMetadata
    scoring_counts: dict[str, int]
    subject_usage: UsageSummary | None
    judge_usage: UsageSummary | None


class AccountingMetric(ChartMetric):
    value: str | float | int | None


class AnalysisUsage(BaseModel):
    grain: Literal["run", "selected_result", "batch_total"]
    accounting_run_count: int
    purposes: dict[str, dict[str, AccountingMetric]]


class AnalysisMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")
    # Old sealed A01 captures have no chart facts. Null explicitly requires refresh.
    usage: AnalysisUsage | None = None
    charts: AnalysisCharts | None = None
    evaluation_series: list[AnalysisEvaluationSeries] | None = None
    captured_at: str | None = None
    coverage: str | dict[str, str | int | None] | None = None
    scores: AnalysisScores | None = None
    series: list[AnalysisSeries] = Field(default_factory=list)
    intervals: list[AnalysisSeries] = Field(default_factory=list)
    approvals: list[AnalysisSeries] = Field(default_factory=list)


class AnalysisSummary(BaseModel):
    metrics: AnalysisMetrics
    grain: Literal["hour", "day"]
    timezone: str
    watermark: str
    metric_version: str


class AnalysisPreference(BaseModel):
    timezone: str | None
    revision: int = Field(ge=0)


class AnalysisPreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)
    timezone: str | None

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ValueError, ZoneInfoNotFoundError):
                raise ValueError("invalid_analysis_timezone") from None
        return value


class AnalysisRun(BaseModel):
    run_id: str
    family: str | None = None
    purpose: str | None = None
    execution_mode: str | None = None
    status: str | None = None
    admitted_at: str | None = None
    terminal_at: str | None = None
    admission_configuration_id: str | None = None


class AnalysisRunPage(BaseModel):
    availability: Literal["available", "retained_data_unavailable"]
    watermark: str
    items: list[AnalysisRun]
    next_cursor: str | None


class ComparisonContext(BaseModel):
    start: str
    end: str
    grain: Literal["hour", "day"]
    timezone: str
    filters: dict[str, str | list[str]]
    selection_mode: Literal["explicit", "all_matching"]
    detail_run_ids: list[str] = Field(max_length=5)
