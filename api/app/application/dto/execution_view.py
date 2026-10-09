"""Public, non-recursive execution views. Private activity payloads are never fields.

Optional observations stay null; a zero duration/token count is an observation,
not a missing-data marker. Opaque cursors are supplied by the query layer.
"""

from datetime import UTC
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.domain.execution.family import RunFamily
from app.domain.execution.run import RunStatus

UTCDateTime = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(UTC))]
NonNegative = Annotated[int, Field(ge=0)]
Identifier = Annotated[str, Field(min_length=1)]


class PublicViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ViewScope(PublicViewModel):
    owner_user_id: Identifier | None = None
    team_id: Identifier | None = None

    @model_validator(mode="after")
    def exactly_one_owner(self) -> Self:
        if (self.owner_user_id is None) == (self.team_id is None):
            raise ValueError("exactly one owner scope is required")
        return self


class MissingInterval(PublicViewModel):
    start: UTCDateTime | None
    end: UTCDateTime | None
    reason: Identifier
    start_order: NonNegative | None = None
    end_order: NonNegative | None = None

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start and self.end and self.end < self.start:
            raise ValueError("missing interval end precedes start")
        return self


class Completeness(PublicViewModel):
    state: Literal["complete", "partial", "unavailable", "rebuilding"]
    missing_fields: list[str]
    missing_intervals: list[MissingInterval]


class SourceReference(PublicViewModel):
    entity_type: Identifier
    entity_id: Identifier
    session_id: str | None = None


class ArtifactReference(PublicViewModel):
    artifact_id: Identifier
    version: Annotated[int, Field(ge=1)]
    availability: Literal["available", "unavailable", "pending", "unknown"]


class CitationReference(PublicViewModel):
    resource_kind: Literal["knowledge_base", "file"] = "knowledge_base"
    file_id: str | None = None
    content_digest: str | None = None
    object_identity: str | None = None
    knowledge_base_id: str | None = None
    citation_id: Identifier
    version_id: str | None = None
    document_revision_id: str | None = None
    doc_id: str | None = None
    chunk_id: str | None = None
    page_no: Annotated[int, Field(ge=1)] | None = None
    anchor: str | None = None
    availability: Literal["available", "unavailable", "unknown"]


class ContentReference(PublicViewModel):
    """An opaque authorized-reader identity, never a storage URL or private payload."""

    content_id: Identifier
    media_type: str | None = None
    byte_length: NonNegative | None = None
    truncated: bool
    availability: Literal["available", "unavailable", "unknown"]


class ConfigurationSummary(PublicViewModel):
    version_unpinned: bool | None = None
    configuration_revision: str | None = None
    model_revision: str | None = None
    prompt_revision: str | None = None
    tool_contract_revision: str | None = None
    temperature: float | None = None
    top_p: float | None = None


class UsagePurposeSummary(PublicViewModel):
    calls: NonNegative
    unknown_usage_calls: NonNegative
    unknown_cost_calls: NonNegative
    known_input_count: NonNegative
    known_output_count: NonNegative
    known_cost_usd: str


class RunView(PublicViewModel):
    run_id: UUID
    family: RunFamily
    status: RunStatus
    wait_reason: str | None
    scope: ViewScope
    source: SourceReference | None
    purpose: Literal["production", "evaluation_subject", "evaluation_judge", "unknown"]
    projection_revision: NonNegative
    as_of: UTCDateTime | None
    latest_available: UTCDateTime | None
    completeness: Completeness
    capabilities: list[str]
    schema_version: Annotated[int, Field(ge=1)] = 1
    admitted_at: UTCDateTime | None = None
    terminal_at: UTCDateTime | None = None
    duration_ms: NonNegative | None = None
    public_summary: str | None = None
    configuration: ConfigurationSummary | None = None
    execution_mode: Literal["production", "recorded", "isolated", "unknown"] | None = None
    first_replayable_cursor: str | None = None
    usage: (
        dict[
            Literal["production", "evaluation_subject", "evaluation_judge", "unknown"],
            UsagePurposeSummary,
        ]
        | None
    ) = None


class StepView(PublicViewModel):
    step_id: Identifier
    run_id: UUID
    activity_id: UUID | None = None
    invocation_id: UUID | None = None
    attempt_id: str | None = None
    logical_step_id: str | None = None
    parent_step_id: str | None = None
    semantic_key: str | None = None
    relationship: Literal["direct", "derived", "unknown"] = "unknown"
    kind: Literal["model", "tool", "activity", "phase", "approval", "clarification", "unknown"]
    status: Literal[
        "new",
        "queued",
        "running",
        "waiting",
        "completed",
        "failed",
        "cancelled",
        "deferred",
        "unknown",
    ]
    started_at: UTCDateTime | None = None
    ended_at: UTCDateTime | None = None
    duration_ms: NonNegative | None = None
    progress: Annotated[float, Field(ge=0, le=100)] | None = None
    phase: str | None = None
    progress_status: str | None = None
    first_persisted_output_at: UTCDateTime | None = None
    wait_reason: str | None = None
    end_reason: str | None = None
    business_outcome: Literal["success", "failure", "unknown"] | None = None
    tool_name: str | None = None
    tool_contract_revision: str | None = None
    public_summary: str | None = None
    input_ref: ContentReference | None = None
    output_ref: ContentReference | None = None
    artifact_refs: list[ArtifactReference] | None = None
    citation_refs: list[CitationReference] | None = None
    configuration: ConfigurationSummary | None = None
    projection_revision: NonNegative
    completeness: Completeness
    schema_version: Annotated[int, Field(ge=1)] = 1


class ApprovalView(PublicViewModel):
    approval_id: Identifier
    subject_activity_id: str | None = None
    approval_kind: str | None = None
    decision: str | None = None
    status: str | None = None


class MessageView(PublicViewModel):
    message_id: Identifier
    role: str | None = None
    public_summary: str | None = None
    step_id: str | None = None
    progress: float | None = None
    phase: str | None = None
    progress_status: str | None = None


class StepViewPage(PublicViewModel):
    items: list[StepView]
    next_cursor: str | None
    revision: NonNegative
    completeness: Completeness
    hidden_count: NonNegative
    at: str


class RunViewPage(PublicViewModel):
    items: list[RunView]
    next_cursor: str | None
    revision: str
    completeness: Completeness


class TimelineBucket(PublicViewModel):
    start: UTCDateTime
    end: UTCDateTime
    count: NonNegative
    first_at: str
    last_at: str
    formal_count: NonNegative


class TimelineKeyEvent(PublicViewModel):
    at: str
    observed_at: UTCDateTime
    kinds: list[Literal["run", "step", "approval", "artifact", "message"]]


class TimelineView(PublicViewModel):
    run_id: UUID
    revision: NonNegative
    buckets: list[TimelineBucket]
    key_events: list[TimelineKeyEvent]
    at: str | None
    completeness: Completeness
    latest_available: UTCDateTime | None


class ViewPage(PublicViewModel):
    approvals: list[ApprovalView] = Field(default_factory=list)
    artifacts: list[ArtifactReference] = Field(default_factory=list)
    messages: list[MessageView] = Field(default_factory=list)
    at: str | None = None
    hidden_count: NonNegative = 0
    run: RunView
    steps: list[StepView]
    next_cursor: str | None
    revision: NonNegative

    @model_validator(mode="after")
    def coherent_boundary(self) -> Self:
        if self.revision != self.run.projection_revision:
            raise ValueError("page and run revisions differ")
        if any(
            step.run_id != self.run.run_id or step.projection_revision != self.revision
            for step in self.steps
        ):
            raise ValueError("steps must belong to the same run and revision")
        return self
