"""Immutable server-owned artifact production identity and boundary selection."""

from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from app.domain.json_values import deep_freeze_json
from app.domain.models.scope import OwnerScope


class ArtifactProducer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: OwnerScope
    run_id: UUID
    activity_id: UUID
    generation: int = Field(ge=0)
    claim_generation: int = Field(ge=1)
    invocation_id: UUID | None = None

    @property
    def operation_id(self) -> UUID:
        scope = f"team:{self.scope.team_id}" if self.scope.team_id else f"user:{self.scope.user_id}"
        return uuid5(
            NAMESPACE_URL,
            f"artifact-write:{scope}:{self.run_id}:{self.activity_id}:{self.generation}:{self.claim_generation}",
        )


def visible_versions(records: list[dict], boundary_position: int) -> list[dict]:
    """Keep input order and multiplicity; only confirmed versions enter a cut.

    Plain position records are already-confirmed legacy callers. Rich records
    must explicitly be bound and carry a non-null confirmed boundary.
    """
    if boundary_position < 0:
        raise ValueError("boundary must be non-negative")
    return [
        record
        for record in records
        if record.get("binding_status", "bound") == "bound"
        and isinstance(record.get("boundary", record.get("position")), int)
        and record.get("boundary", record.get("position")) <= boundary_position
    ]


class ArtifactVersionProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: UUID
    artifact_id: str
    version: int = Field(ge=1)
    producer_identity: str
    evidence_kind: Literal["direct", "derived", "unknown"]
    binding_status: Literal["pending", "bound", "unavailable"]
    content_digest: str | None
    producer_run_id: UUID | None = None
    activity_id: UUID | None = None
    attempt_id: str | None = None
    invocation_id: UUID | None = None
    produced_event_id: UUID | None = None
    boundary: int | None = None
    availability: Literal["available", "unavailable", "pending", "unknown"] = "available"
    producer_step_ids: tuple[str, ...] = ()
    evidence: dict[str, JsonValue] = Field(default_factory=dict)
    citation_refs: list[JsonValue] = Field(default_factory=list)
    content_ref: dict[str, JsonValue] | None = None

    @field_validator("evidence", "citation_refs", "producer_step_ids", mode="before")
    @classmethod
    def _empty_legacy_metadata(cls, value, info):
        return ({} if info.field_name == "evidence" else []) if value is None else value

    @field_validator("evidence", "citation_refs", "content_ref", mode="after")
    @classmethod
    def _immutable_metadata(cls, value):
        return deep_freeze_json(value)

    @model_validator(mode="after")
    def _binding_evidence(self):
        references = (
            self.producer_run_id,
            self.activity_id,
            self.attempt_id,
            self.invocation_id,
            self.produced_event_id,
            self.boundary,
        )
        if self.binding_status == "pending" and any(value is not None for value in references):
            raise ValueError("pending producer authority belongs in private receipt")
        if self.binding_status == "bound" and any(
            value is None for value in (self.producer_run_id, self.produced_event_id, self.boundary)
        ):
            raise ValueError("bound producer requires exact confirmed event boundary")
        return self
