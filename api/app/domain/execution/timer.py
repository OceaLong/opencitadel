"""Durable, idempotent command timer requests."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.domain.execution.commands import CommandEnvelope, normalize_utc


class ScheduledCommandRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timer_id: UUID
    due_at: datetime
    command: CommandEnvelope
    cancellation_event_types: frozenset[str]
    cancellation_activity_id: UUID | None = None

    @field_validator("due_at")
    @classmethod
    def _utc_due_at(cls, value: datetime) -> datetime:
        return normalize_utc(value)

    @field_validator("cancellation_event_types")
    @classmethod
    def _valid_event_types(cls, value: frozenset[str]) -> frozenset[str]:
        if any(not item.strip() for item in value):
            raise ValueError("cancellation event types must not be empty")
        return frozenset(item.strip() for item in value)


__all__ = ["ScheduledCommandRequest"]
