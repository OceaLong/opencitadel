"""Hash/replay tests that catch non-canonical or unverifiable journals."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.kernel.domain.events import (
    ZERO_HASH,
    EventIntegrityError,
    StoredEvent,
    event_hash,
    replay,
)
from app.kernel.domain.types import OwnerScopeRef

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
RUN_ID = UUID("00000000-0000-0000-0000-000000000101")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000201")


def _event(*, public_payload: dict[str, object], current_hash: str = "") -> StoredEvent:
    return StoredEvent(
        event_id=EVENT_ID,
        run_id=RUN_ID,
        version=1,
        type="RunStarted",
        schema_version=1,
        public_payload=public_payload,
        private_payload_ciphertext="ciphertext-v1",
        previous_hash=ZERO_HASH,
        hash=current_hash,
        owner_scope=OwnerScopeRef.personal("user-1"),
        actor_user_id="user-1",
        request_id="request-1",
        causation_id=UUID("00000000-0000-0000-0000-000000000301"),
        correlation_id=RUN_ID,
        occurred_at=NOW,
    )


def test_event_hash_is_canonical_and_chained() -> None:
    """Changing key order must not change a hash; changing the chain must."""

    first = _event(public_payload={"b": 2, "a": 1})
    reordered = _event(public_payload={"a": 1, "b": 2})

    assert event_hash(ZERO_HASH, first) == event_hash(ZERO_HASH, reordered)
    assert event_hash("1" * 64, first) != event_hash(ZERO_HASH, first)


def test_replay_rejects_a_tampered_payload() -> None:
    """A payload mutation after append must poison replay."""

    unsigned = _event(public_payload={"workflow": "agent", "title": "original"})
    signed = unsigned.model_copy(update={"hash": event_hash(ZERO_HASH, unsigned)})
    tampered = signed.model_copy(
        update={"public_payload": {"workflow": "agent", "title": "tampered"}}
    )

    with pytest.raises(EventIntegrityError, match="hash"):
        replay((tampered,))


def test_replay_rejects_a_version_gap() -> None:
    """Missing an event must never produce a plausible current state."""

    unsigned = _event(public_payload={"workflow": "agent"}).model_copy(update={"version": 2})
    signed = unsigned.model_copy(update={"hash": event_hash(ZERO_HASH, unsigned)})

    with pytest.raises(EventIntegrityError, match="version"):
        replay((signed,))
