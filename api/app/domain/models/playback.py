"""Typed boundary for one committed execution-view history cut."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlaybackBoundary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    formal_position: int = Field(ge=0)
    progress_position: int = Field(ge=0)
    observed_order: int = Field(ge=0)
    projection_revision: int = Field(ge=0)
    observed_at: datetime
    projector_version: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_committed_cut(self) -> PlaybackBoundary:
        if self.observed_at.utcoffset() is None:
            raise ValueError("playback boundary time must be timezone-aware")
        if self.projection_revision != self.observed_order:
            raise ValueError("playback boundary revision/order mismatch")
        return self
