"""Typed input envelope accepted by the journal command service."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .types import OwnerScopeRef, Workflow


class CommandEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    command_id: UUID
    run_id: UUID
    workflow: Workflow
    type: str = Field(min_length=1, max_length=120)
    schema_version: int = Field(default=1, ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_stream_version: int | None = Field(default=None, ge=0)
    owner_scope: OwnerScopeRef
    actor_user_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    submitted_at: datetime
