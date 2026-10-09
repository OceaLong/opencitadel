"""Caller inputs cannot supply private captures, owners, manifests or storage keys."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.interfaces.schemas.base import Response


class ExportSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["explicit", "all_matching"] = "all_matching"
    run_ids: list[UUID] = Field(default_factory=list, max_length=100000)
    excluded_run_ids: list[UUID] = Field(default_factory=list, max_length=100000)
    filters: dict[str, str | list[str]] = Field(default_factory=dict)
    grain: Literal["hour", "day"] = "day"
    timezone: str = "UTC"


class FilterExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_kind: Literal["filter"]
    request_id: str = Field(min_length=1, max_length=128)
    format: Literal["csv", "json"]
    selection: ExportSelection


class ComparisonExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_kind: Literal["comparison"]
    request_id: str = Field(min_length=1, max_length=128)
    format: Literal["csv", "json"]
    comparison_id: UUID
    revision: int = Field(ge=1)


class BatchExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_kind: Literal["batch"]
    request_id: str = Field(min_length=1, max_length=128)
    format: Literal["csv", "json"]
    batch_id: UUID
    evaluation_revision: int = Field(ge=0)
    source: Literal["rule", "human", "model"]
    dimension: str = Field(min_length=1, max_length=255)
    rubric_id: UUID
    snapshot_id: UUID | None = None
    batch_revision: int | None = Field(default=None, ge=0)
    timezone: str = "UTC"


ExportCreate = Annotated[
    FilterExport | ComparisonExport | BatchExport, Field(discriminator="source_kind")
]


class ExportJob(BaseModel):
    id: UUID
    status: Literal["queued", "running", "ready", "failed", "invalidated", "expired"]
    created_at: datetime | None = None
    expires_at: datetime | None = None
    format: Literal["csv", "json"] | None = None
    failure_code: str | None = None
    row_count: int | None = None


class ExportError(BaseModel):
    """Export-specific machine code, including propagated fixed-source errors."""

    code: str = Field(
        min_length=1,
        description="Export, comparison, summary or execution authorization error code",
    )


EXPORT_RESPONSES = {
    status: {"model": Response[ExportError], "description": description}
    for status, description in {
        "4XX": "Export client error (validation uses 400)",
        400: "invalid_argument or invalid export request",
        403: "permission_denied",
        404: "export_not_found or source not found",
        409: "Fixed source conflict, unavailable, invalidated or not ready",
        410: "export_expired",
        413: "export_capacity_exceeded",
        429: "Export quota exceeded",
        503: "projection_rebuilding",
    }.items()
}
