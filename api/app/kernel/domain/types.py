"""Closed protocol enums and tenant identity values."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator


class Workflow(StrEnum):
    AGENT = "agent"
    KNOWLEDGE_INGEST = "knowledge_ingest"


class RunStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"
    PURGED = "purged"


class EffectSafety(StrEnum):
    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"


class EffectStatus(StrEnum):
    BLOCKED = "blocked"
    READY = "ready"
    CLAIMED = "claimed"
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class OwnerScopeRef(BaseModel):
    """Immutable personal or team ownership embedded in every kernel record."""

    model_config = ConfigDict(frozen=True)

    owner_user_id: str | None = None
    team_id: str | None = None

    @model_validator(mode="after")
    def _require_exactly_one_owner(self) -> Self:
        if (self.owner_user_id is None) == (self.team_id is None):
            raise ValueError("owner scope requires exactly one personal or team owner")
        if self.owner_user_id is not None and not self.owner_user_id.strip():
            raise ValueError("personal owner must not be blank")
        if self.team_id is not None and not self.team_id.strip():
            raise ValueError("team owner must not be blank")
        return self

    @classmethod
    def personal(cls, user_id: str) -> Self:
        return cls(owner_user_id=user_id)

    @classmethod
    def team(cls, team_id: str) -> Self:
        return cls(team_id=team_id)
