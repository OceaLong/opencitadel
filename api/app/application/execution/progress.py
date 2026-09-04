"""Off-stream activity progress telemetry.

Progress reports are display-only telemetry: they never influence a decision,
never change aggregate state, and can arrive at high frequency (model streaming,
long resource builds). Routing them through the hash-chained, per-scope-locked
event stream made every heartbeat pay the strong-consistency write path and
grew RunState without bound, so they bypass the aggregate entirely and land
directly in the sanitized public event feed via this sink.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActivityProgressRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    activity_id: UUID
    generation: int = Field(ge=0)
    claim_generation: int = Field(ge=1)
    sequence: int = Field(ge=1)
    kind: Literal["step", "message"]
    phase: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    status: Literal["started", "completed"] | None = None
    progress: int = Field(ge=0, le=100)
    message: Annotated[str, Field(max_length=1024)] = ""
    owner_user_id: str | None
    team_id: str | None
    source_entity_type: str | None = None
    source_entity_id: str | None = None
    occurred_at: datetime

    @model_validator(mode="after")
    def _exactly_one_owner_scope(self) -> ActivityProgressRecord:
        if (self.owner_user_id is None) == (self.team_id is None):
            raise ValueError("exactly one of owner_user_id or team_id is required")
        return self

    @property
    def event_id(self) -> UUID:
        """Deterministic identity: redelivery of the same report deduplicates.

        The claim generation participates so a re-executed activity (deferred
        and picked up again) reports a fresh progress series instead of being
        swallowed by the previous execution's rows.
        """
        return uuid5(
            NAMESPACE_URL,
            "opencitadel:activity-progress:"
            f"{self.activity_id}:{self.generation}:{self.claim_generation}:{self.sequence}",
        )


class ActivityProgressSink(Protocol):
    async def record(self, record: ActivityProgressRecord) -> bool:
        """Persist one progress report; True when durably recorded."""
        ...


__all__ = ["ActivityProgressRecord", "ActivityProgressSink"]
