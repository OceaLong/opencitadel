"""Property coverage for deterministic event replay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.kernel.domain.events import ZERO_HASH, StoredEvent, event_hash, replay
from app.kernel.domain.state import apply_event
from app.kernel.domain.types import OwnerScopeRef

RUN_ID = UUID("00000000-0000-0000-0000-000000000101")
CAUSATION_ID = UUID("00000000-0000-0000-0000-000000000301")
NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)


def _stream(event_types: tuple[str, ...], title: str = "Agent") -> tuple[StoredEvent, ...]:
    previous_hash = ZERO_HASH
    events: list[StoredEvent] = []
    for version, event_type in enumerate(event_types, start=1):
        payload: dict[str, object] = {}
        if event_type == "RunStarted":
            payload = {"workflow": "agent", "title": title, "status": "running"}
        unsigned = StoredEvent(
            event_id=UUID(int=1_000 + version),
            run_id=RUN_ID,
            version=version,
            type=event_type,
            schema_version=1,
            public_payload=payload,
            private_payload_ciphertext="ciphertext-v1",
            previous_hash=previous_hash,
            hash="",
            owner_scope=OwnerScopeRef.personal("user-1"),
            actor_user_id="user-1",
            request_id=f"request-{version}",
            causation_id=CAUSATION_ID,
            correlation_id=RUN_ID,
            occurred_at=NOW + timedelta(seconds=version),
        )
        signed = unsigned.model_copy(update={"hash": event_hash(previous_hash, unsigned)})
        events.append(signed)
        previous_hash = signed.hash
    return tuple(events)


@given(
    title=st.text(min_size=1, max_size=40),
    turn_count=st.integers(min_value=0, max_value=20),
)
def test_full_replay_matches_incremental_application(title: str, turn_count: int) -> None:
    """Changing replay batching must never change reconstructed state."""

    types = ("RunStarted",) + ("PromptAccepted", "TurnCompleted") * turn_count
    events = _stream(types, title)
    incremental = None
    for event in events:
        incremental = apply_event(incremental, event)

    assert replay(events) == incremental


def test_replay_rejects_a_second_run_started_event() -> None:
    """A duplicate start must not reset or disguise an existing stream."""

    events = _stream(("RunStarted", "RunStarted"))

    with pytest.raises(ValueError, match="RunStarted"):
        replay(events)
