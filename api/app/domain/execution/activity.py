"""Durable requests, claims, contexts, and outcomes for external work."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.execution.commands import (
    JsonValue,
    deep_freeze_json,
    normalize_utc,
    require_non_empty,
)
from app.domain.execution.context import RunExecutionContext
from app.domain.models.scope import OwnerScopeType


class ActivityRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    activity_id: UUID
    activity_type: str
    aggregate_type: str
    aggregate_id: str
    generation: int = Field(ge=0)
    timeout_at: datetime
    input_ref: str | None
    input_digest: str
    input_payload: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator(
        "activity_type",
        "aggregate_type",
        "aggregate_id",
        "input_digest",
    )
    @classmethod
    def _non_empty_value(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "activity field")
        return require_non_empty(value, field_name=field_name)

    @field_validator("input_ref")
    @classmethod
    def _non_empty_input_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_non_empty(value, field_name="input_ref")

    @field_validator("timeout_at")
    @classmethod
    def _utc_timeout(cls, value: datetime) -> datetime:
        return normalize_utc(value)

    @field_validator("input_payload", mode="after")
    @classmethod
    def _freeze_input_payload(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        frozen = deep_freeze_json(value)
        assert isinstance(frozen, dict)
        return frozen


class ActivityClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: ActivityRequest
    claim_generation: int = Field(ge=1)
    owner_user_id: str | None
    team_id: str | None
    recovered_after_call_started: bool = False

    @model_validator(mode="after")
    def _exactly_one_owner_scope(self) -> ActivityClaim:
        if (self.owner_user_id is None) == (self.team_id is None):
            raise ValueError("exactly one owner scope is required")
        return self


class ActivityContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    worker_id: str
    claim_generation: int = Field(ge=1)
    idempotency_key: str
    owner_user_id: str | None
    team_id: str | None
    run: RunExecutionContext
    heartbeat: Callable[[], Awaitable[bool]] | None = Field(
        default=None,
        exclude=True,
    )
    report_progress: Callable[[dict[str, JsonValue]], Awaitable[bool]] | None = Field(
        default=None, exclude=True
    )

    @field_validator("worker_id", "idempotency_key")
    @classmethod
    def _non_empty_identity(cls, value: str, info: object) -> str:
        return require_non_empty(
            value,
            field_name=getattr(info, "field_name", "activity context"),
        )

    @model_validator(mode="after")
    def _exactly_one_owner_scope(self) -> ActivityContext:
        if (self.owner_user_id is None) == (self.team_id is None):
            raise ValueError("exactly one owner scope is required")
        scope = self.run.owner_scope
        if self.owner_user_id is not None and (
            scope.type != OwnerScopeType.PERSONAL
            or scope.user_id != self.owner_user_id
            or scope.team_id is not None
        ):
            raise ValueError("Activity owner does not match Run owner scope")
        if self.team_id is not None and (
            scope.type != OwnerScopeType.TEAM or scope.team_id != self.team_id
        ):
            raise ValueError("Activity team does not match Run owner scope")
        return self


class ActivityOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["succeeded", "failed", "unknown", "deferred"]
    result_ref: str | None = None
    result_summary: str | None = Field(default=None, max_length=4096)
    failure_code: str | None = Field(default=None, max_length=128)
    retry_after_seconds: float | None = Field(default=None, gt=0, le=300)
    decision_data: dict[str, JsonValue] = Field(default_factory=dict)
    public_data: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _coherent_status(self) -> ActivityOutcome:
        if self.status in {"succeeded", "deferred"} and self.failure_code is not None:
            raise ValueError("successful Activity cannot have a failure code")
        if self.status in {"failed", "unknown"} and not self.failure_code:
            raise ValueError("failed or unknown Activity requires a failure code")
        if self.status == "deferred" and self.retry_after_seconds is None:
            raise ValueError("deferred Activity requires retry_after_seconds")
        if self.status != "deferred" and self.retry_after_seconds is not None:
            raise ValueError("only deferred Activity may set retry_after_seconds")
        return self

    @field_validator("decision_data", "public_data", mode="after")
    @classmethod
    def _freeze_decision_data(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        frozen = deep_freeze_json(value)
        assert isinstance(frozen, dict)
        return frozen

    @classmethod
    def succeeded(
        cls,
        *,
        result_ref: str | None,
        result_summary: str | None = None,
        decision_data: dict[str, JsonValue] | None = None,
        public_data: dict[str, JsonValue] | None = None,
    ) -> ActivityOutcome:
        return cls(
            status="succeeded",
            result_ref=result_ref,
            result_summary=result_summary,
            decision_data=decision_data or {},
            public_data=public_data or {},
        )

    @classmethod
    def failed(cls, *, failure_code: str) -> ActivityOutcome:
        return cls(status="failed", failure_code=failure_code)

    @classmethod
    def unknown(cls, *, failure_code: str) -> ActivityOutcome:
        return cls(status="unknown", failure_code=failure_code)

    @classmethod
    def deferred(cls, *, retry_after_seconds: float) -> ActivityOutcome:
        return cls(
            status="deferred",
            retry_after_seconds=retry_after_seconds,
        )


class ActivityHandler(Protocol):
    activity_type: str
    idempotent: bool

    async def execute(
        self,
        request: ActivityRequest,
        context: ActivityContext,
    ) -> ActivityOutcome: ...


__all__ = [
    "ActivityClaim",
    "ActivityContext",
    "ActivityHandler",
    "ActivityOutcome",
    "ActivityRequest",
]
