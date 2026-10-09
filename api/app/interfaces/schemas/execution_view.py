"""Public execution read contracts. Private receipts are never serialized here."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.application.dto.execution_view import CitationReference, StepView
from app.interfaces.schemas.base import Response


class ExecutionReadError(BaseModel):
    code: Literal[
        "invalid_argument",
        "permission_denied",
        "not_found",
        "revision_conflict",
        "resource_unavailable",
        "projection_rebuilding",
    ]


class ArtifactProvenanceResponse(BaseModel):
    citation_refs: list[CitationReference] = Field(default_factory=list)
    production_order: int | None = Field(
        default=None,
        description="Run-local journal observation order; comparable only within producer_run_id, never a cursor",
    )
    production_observed_at: datetime | None = Field(
        default=None,
        description="Persisted observation time of the confirmed production event, not current artifact update time",
    )
    artifact_id: str
    version: int
    producer_run_id: UUID | None
    producer_step_ids: tuple[str, ...]
    activity_id: UUID | None
    attempt_id: str | None
    invocation_id: UUID | None
    produced_event_id: UUID | None
    binding_status: Literal["pending", "bound", "unavailable"]
    evidence_kind: Literal["direct", "derived", "unknown"]
    availability: Literal["available", "unavailable", "pending", "unknown"]


READ_RESPONSES = {
    status: {
        "model": Response[ExecutionReadError],
        "description": description,
    }
    # A typed 4XX family prevents FastAPI adding its unreachable default 422
    # validation envelope; ExecutionReadRoute maps validation failures to 400.
    for status, description in {
        "4XX": "Execution read client error (validation uses 400)",
        400: "invalid_argument",
        403: "permission_denied",
        404: "not_found",
        409: "revision_conflict or resource_unavailable",
        503: "projection_rebuilding",
    }.items()
}


class StepDetailResponse(StepView):
    at: str
