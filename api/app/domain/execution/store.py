"""Event Store contracts, optimistic concurrency, and hash-chain verification."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.domain.execution.commands import normalize_utc, require_non_empty
from app.domain.execution.events import NewEvent, StoredEvent
from app.domain.execution.serialization import canonical_json_bytes

ZERO_HASH = "0" * 64


class ExecutionStoreError(RuntimeError):
    pass


class OptimisticConcurrencyError(ExecutionStoreError):
    def __init__(self, *, expected_version: int, actual_version: int) -> None:
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(f"expected stream version {expected_version}, actual {actual_version}")


class CorruptEventStreamError(ExecutionStoreError):
    def __init__(self, *, stream_version: int, reason: str) -> None:
        self.stream_version = stream_version
        self.reason = reason
        super().__init__(f"corrupt event stream at version {stream_version}: {reason}")


class StreamOwnerScopeMismatchError(ExecutionStoreError):
    def __init__(
        self,
        *,
        stream: StreamRef,
    ) -> None:
        self.stream = stream
        super().__init__(
            "append owner scope does not match the immutable stream owner: "
            f"{stream.stream_type}/{stream.stream_id}"
        )


class PayloadTooLargeError(ExecutionStoreError):
    def __init__(self, *, size: int, limit: int) -> None:
        self.size = size
        self.limit = limit
        super().__init__(f"event payload is {size} bytes; limit is {limit}")


class StreamRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stream_type: str
    stream_id: str

    @field_validator("stream_type", "stream_id")
    @classmethod
    def _non_empty_identity(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "stream identity")
        return require_non_empty(value, field_name=field_name)


class AppendContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_user_id: str | None
    team_id: str | None
    correlation_id: UUID
    causation_id: UUID | None
    occurred_at: datetime

    @field_validator("owner_user_id", "team_id")
    @classmethod
    def _non_empty_optional_scope(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "owner scope")
        return require_non_empty(value, field_name=field_name)

    @field_validator("occurred_at")
    @classmethod
    def _utc_timestamp(cls, value: datetime) -> datetime:
        return normalize_utc(value)

    @model_validator(mode="after")
    def _exactly_one_owner_scope(self) -> AppendContext:
        if (self.owner_user_id is None) == (self.team_id is None):
            raise ValueError("exactly one of owner_user_id or team_id is required")
        return self


class AppendResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    events: tuple[StoredEvent, ...]
    first_position: int | None
    last_position: int | None


class EventStore(Protocol):
    async def load_stream(
        self,
        stream_type: str,
        stream_id: str,
        *,
        after_version: int = 0,
        expected_previous_hash: str | None = None,
    ) -> tuple[StoredEvent, ...]: ...

    async def append(
        self,
        stream: StreamRef,
        expected_version: int,
        events: Sequence[NewEvent],
        context: AppendContext,
    ) -> AppendResult: ...

    async def read_all(
        self,
        *,
        after_position: int,
        limit: int,
    ) -> tuple[StoredEvent, ...]: ...


def calculate_event_hash(event: StoredEvent) -> str:
    stable_fields = {
        "event_id": str(event.event_id),
        "stream_type": event.stream_type,
        "stream_id": event.stream_id,
        "stream_version": event.stream_version,
        "event_type": event.event_type,
        "event_schema_version": event.event_schema_version,
        "public_payload": event.public_payload,
        "internal_payload": event.internal_payload,
        "secret_ref": event.secret_ref,
        "owner_user_id": event.owner_user_id,
        "team_id": event.team_id,
        "correlation_id": str(event.correlation_id),
        "causation_id": str(event.causation_id) if event.causation_id else None,
        "occurred_at": event.occurred_at,
        "prev_hash": event.prev_hash,
    }
    return hashlib.sha256(canonical_json_bytes(stable_fields)).hexdigest()


def verify_event_hashes(events: Sequence[StoredEvent]) -> None:
    """Verify independently readable events without assuming stream adjacency."""
    for event in events:
        expected_hash = calculate_event_hash(event)
        if not hmac.compare_digest(event.event_hash, expected_hash):
            raise CorruptEventStreamError(
                stream_version=event.stream_version,
                reason="event hash mismatch",
            )


def verify_stream(
    events: Sequence[StoredEvent],
    *,
    previous_hash: str = ZERO_HASH,
    previous_version: int = 0,
    stream_identity: tuple[str, str] | None = None,
    stream_owner_scope: tuple[str | None, str | None] | None = None,
) -> None:
    if previous_version < 0:
        raise ValueError("previous_version must not be negative")
    for event in events:
        identity = (event.stream_type, event.stream_id)
        if stream_identity is None:
            stream_identity = identity
        elif identity != stream_identity:
            raise CorruptEventStreamError(
                stream_version=event.stream_version,
                reason="stream identity changed",
            )
        owner_scope = (event.owner_user_id, event.team_id)
        if stream_owner_scope is None:
            stream_owner_scope = owner_scope
        elif owner_scope != stream_owner_scope:
            raise CorruptEventStreamError(
                stream_version=event.stream_version,
                reason="stream owner scope changed",
            )
        if event.stream_version != previous_version + 1:
            raise CorruptEventStreamError(
                stream_version=event.stream_version,
                reason="stream versions are not contiguous",
            )
        if not hmac.compare_digest(event.prev_hash, previous_hash):
            raise CorruptEventStreamError(
                stream_version=event.stream_version,
                reason="previous hash mismatch",
            )
        expected_hash = calculate_event_hash(event)
        if not hmac.compare_digest(event.event_hash, expected_hash):
            raise CorruptEventStreamError(
                stream_version=event.stream_version,
                reason="event hash mismatch",
            )
        previous_hash = event.event_hash
        previous_version = event.stream_version


__all__ = [
    "ZERO_HASH",
    "AppendContext",
    "AppendResult",
    "CorruptEventStreamError",
    "EventStore",
    "ExecutionStoreError",
    "OptimisticConcurrencyError",
    "PayloadTooLargeError",
    "StreamOwnerScopeMismatchError",
    "StreamRef",
    "calculate_event_hash",
    "verify_event_hashes",
    "verify_stream",
]
