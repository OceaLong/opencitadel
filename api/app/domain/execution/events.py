"""Immutable facts proposed for append to an execution stream."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.execution.commands import (
    JsonValue,
    deep_freeze_json,
    normalize_utc,
    require_non_empty,
    validate_json,
)

_SECRET_KEY_MARKERS = (
    "authorization",
    "cookie",
    "password",
    "passwd",
    "privatekey",
    "secret",
    "token",
)


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _reject_public_secrets(value: object, *, path: str = "public_payload") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = _normalized_key(key)
            if any(marker in normalized for marker in _SECRET_KEY_MARKERS):
                raise ValueError(f"{path}.{key} is a secret-bearing public field")
            _reject_public_secrets(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_public_secrets(item, path=f"{path}[{index}]")


class NewEvent(BaseModel):
    """An event without storage-assigned identity, position, or timestamp."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: str
    event_schema_version: int = Field(ge=1)
    public_payload: dict[str, JsonValue]
    internal_payload: dict[str, JsonValue]
    secret_ref: str | None = None

    @field_validator("event_type")
    @classmethod
    def _non_empty_event_type(cls, value: str) -> str:
        return require_non_empty(value, field_name="event_type")

    @field_validator("secret_ref")
    @classmethod
    def _non_empty_secret_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_non_empty(value, field_name="secret_ref")

    @field_validator("public_payload", mode="before")
    @classmethod
    def _safe_public_json(cls, value: object) -> object:
        validate_json(value, path="public_payload")
        _reject_public_secrets(value)
        return value

    @field_validator("internal_payload", mode="before")
    @classmethod
    def _strict_internal_json(cls, value: object) -> object:
        return validate_json(value, path="internal_payload")

    @field_validator("public_payload", "internal_payload", mode="after")
    @classmethod
    def _immutable_payloads(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        frozen = deep_freeze_json(value)
        assert isinstance(frozen, dict)
        return frozen


class StoredEvent(BaseModel):
    """An immutable event with storage-assigned ordering and integrity data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    position: int = Field(ge=1)
    event_id: UUID
    stream_type: str
    stream_id: str
    stream_version: int = Field(ge=1)
    event_type: str
    event_schema_version: int = Field(ge=1)
    public_payload: dict[str, JsonValue]
    internal_payload: dict[str, JsonValue]
    secret_ref: str | None
    owner_user_id: str | None
    team_id: str | None
    correlation_id: UUID
    causation_id: UUID | None
    occurred_at: datetime
    prev_hash: str = Field(min_length=64, max_length=64)
    event_hash: str = Field(min_length=64, max_length=64)

    @field_validator("stream_type", "stream_id", "event_type")
    @classmethod
    def _non_empty_identity(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "event identity")
        return require_non_empty(value, field_name=field_name)

    @field_validator("owner_user_id", "team_id", "secret_ref")
    @classmethod
    def _non_empty_optional_value(
        cls,
        value: str | None,
        info: object,
    ) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "optional event field")
        return require_non_empty(value, field_name=field_name)

    @field_validator("public_payload", mode="before")
    @classmethod
    def _safe_public_json(cls, value: object) -> object:
        validate_json(value, path="public_payload")
        _reject_public_secrets(value)
        return value

    @field_validator("internal_payload", mode="before")
    @classmethod
    def _strict_internal_json(cls, value: object) -> object:
        return validate_json(value, path="internal_payload")

    @field_validator("public_payload", "internal_payload", mode="after")
    @classmethod
    def _immutable_payloads(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        frozen = deep_freeze_json(value)
        assert isinstance(frozen, dict)
        return frozen

    @field_validator("occurred_at")
    @classmethod
    def _utc_occurred_at(cls, value: datetime) -> datetime:
        return normalize_utc(value)

    @model_validator(mode="after")
    def _exactly_one_owner_scope(self) -> StoredEvent:
        if (self.owner_user_id is None) == (self.team_id is None):
            raise ValueError("exactly one of owner_user_id or team_id is required")
        return self


__all__ = ["NewEvent", "StoredEvent"]
