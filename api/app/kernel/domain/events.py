"""Append-only event values, canonical hashing, and verified replay."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .types import OwnerScopeRef

ZERO_HASH = "0" * 64


class EventIntegrityError(RuntimeError):
    """Raised when an event stream cannot be proven complete and untampered."""


class NewEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID
    type: str = Field(min_length=1, max_length=120)
    schema_version: int = Field(default=1, ge=1)
    public_payload: dict[str, Any] = Field(default_factory=dict)
    private_payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class StoredEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID
    run_id: UUID
    version: int = Field(ge=1)
    type: str = Field(min_length=1, max_length=120)
    schema_version: int = Field(ge=1)
    public_payload: dict[str, Any] = Field(default_factory=dict)
    private_payload_ciphertext: str
    previous_hash: str = Field(min_length=64, max_length=64)
    hash: str = Field(min_length=0, max_length=64)
    owner_scope: OwnerScopeRef
    actor_user_id: str
    request_id: str
    causation_id: UUID
    correlation_id: UUID
    occurred_at: datetime


def event_hash(previous_hash: str, event: StoredEvent) -> str:
    """Return the canonical SHA-256 hash for one event and its predecessor."""

    body = event.model_dump(mode="json", exclude={"hash", "previous_hash"})
    body["previous_hash"] = previous_hash
    canonical = json.dumps(
        body,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def replay(events: tuple[StoredEvent, ...]):
    """Verify a complete stream and deterministically rebuild its Run state."""

    from .state import RunState, apply_event

    state: RunState | None = None
    previous_hash = ZERO_HASH
    for expected_version, event in enumerate(events, start=1):
        if event.version != expected_version:
            raise EventIntegrityError(
                f"event version gap: expected {expected_version}, got {event.version}"
            )
        if event.previous_hash != previous_hash:
            raise EventIntegrityError(f"previous hash mismatch at version {event.version}")
        calculated = event_hash(previous_hash, event)
        if event.hash != calculated:
            raise EventIntegrityError(f"event hash mismatch at version {event.version}")
        state = apply_event(state, event)
        previous_hash = event.hash
    return state
