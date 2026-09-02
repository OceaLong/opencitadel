"""Durable declarations for non-deterministic work and timers."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .types import EffectSafety


class EffectDeclaration(BaseModel):
    model_config = ConfigDict(frozen=True)

    effect_id: UUID
    invocation_id: UUID
    type: str = Field(min_length=1, max_length=120)
    safety: EffectSafety
    request: dict[str, Any] = Field(default_factory=dict)
    public_summary: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    max_attempts: int = Field(default=1, ge=1, le=20)
    requires_approval: bool = False
    approval_id: UUID | None = None
    reviewer_user_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_approval_identity(self):
        if self.requires_approval and (self.approval_id is None or not self.reviewer_user_ids):
            raise ValueError("approval requires an identity and at least one reviewer")
        if not self.requires_approval and self.approval_id is not None:
            raise ValueError("unapproved Effect cannot carry an approval identity")
        return self


class TimerDeclaration(BaseModel):
    model_config = ConfigDict(frozen=True)

    timer_id: UUID
    due_at: datetime
    command_type: str = Field(min_length=1, max_length=120)
    command_payload: dict[str, Any] = Field(default_factory=dict)
