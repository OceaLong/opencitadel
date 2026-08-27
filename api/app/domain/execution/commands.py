"""Immutable command envelope and payload validation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from app.domain.json_values import deep_freeze_json, validate_json


def require_non_empty(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


class CommandEnvelope(BaseModel):
    """A tenant-bound, idempotent request addressed to one event stream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command_id: UUID
    command_type: str
    command_schema_version: int = Field(ge=1)
    stream_type: str
    stream_id: str
    expected_stream_version: int | None = Field(default=None, ge=0)
    owner_user_id: str | None
    team_id: str | None
    correlation_id: UUID
    causation_id: UUID | None
    issued_at: datetime
    payload: dict[str, JsonValue]
    payload_digest: str | None = None

    @field_validator("command_type", "stream_type", "stream_id")
    @classmethod
    def _non_empty_identity(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "identity")
        return require_non_empty(value, field_name=field_name)

    @field_validator("owner_user_id", "team_id")
    @classmethod
    def _non_empty_optional_scope(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "owner scope")
        return require_non_empty(value, field_name=field_name)

    @field_validator("issued_at")
    @classmethod
    def _utc_timestamp(cls, value: datetime) -> datetime:
        return normalize_utc(value)

    @field_validator("payload", mode="before")
    @classmethod
    def _strict_json_payload(cls, value: object) -> object:
        return validate_json(value)

    @field_validator("payload", mode="after")
    @classmethod
    def _immutable_payload(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        frozen = deep_freeze_json(value)
        assert isinstance(frozen, dict)
        return frozen

    @field_validator("payload_digest")
    @classmethod
    def _valid_payload_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not value.startswith("sha256:")
            or len(value) != 71
            or any(character not in "0123456789abcdef" for character in value[7:])
        ):
            raise ValueError("payload_digest must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def _exactly_one_owner_scope(self) -> CommandEnvelope:
        if (self.owner_user_id is None) == (self.team_id is None):
            raise ValueError("exactly one of owner_user_id or team_id is required")
        if self.payload_digest is not None and self.payload:
            raise ValueError("an omitted payload digest requires an empty payload")
        return self


class RegisteredCommand(BaseModel):
    """Application-facing command before tenant and causation are attached."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command_id: UUID
    command_type: str
    command_schema_version: int = Field(default=1, ge=1)
    run_id: UUID
    expected_stream_version: int | None = Field(default=None, ge=0)
    payload: dict[str, JsonValue]

    @field_validator("command_type")
    @classmethod
    def _non_empty_type(cls, value: str) -> str:
        return require_non_empty(value, field_name="command_type")

    @field_validator("payload", mode="before")
    @classmethod
    def _strict_payload(cls, value: object) -> object:
        return validate_json(value)

    @field_validator("payload", mode="after")
    @classmethod
    def _freeze_payload(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        frozen = deep_freeze_json(value)
        assert isinstance(frozen, dict)
        return frozen


class CommandContext(BaseModel):
    """Authenticated metadata supplied by an application boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_user_id: str | None
    team_id: str | None
    correlation_id: UUID
    causation_id: UUID | None
    issued_at: datetime

    @field_validator("owner_user_id", "team_id")
    @classmethod
    def _non_empty_scope(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return require_non_empty(
            value,
            field_name=getattr(info, "field_name", "owner scope"),
        )

    @field_validator("issued_at")
    @classmethod
    def _normalize_issued_at(cls, value: datetime) -> datetime:
        return normalize_utc(value)

    @model_validator(mode="after")
    def _exactly_one_owner_scope(self) -> CommandContext:
        if (self.owner_user_id is None) == (self.team_id is None):
            raise ValueError("exactly one of owner_user_id or team_id is required")
        return self


__all__ = [
    "CommandContext",
    "CommandEnvelope",
    "JsonValue",
    "RegisteredCommand",
    "deep_freeze_json",
    "validate_json",
]
