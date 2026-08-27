from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domain.execution.events import StoredEvent
from app.domain.execution.store import (
    CorruptEventStreamError,
    calculate_event_hash,
    verify_event_hashes,
    verify_stream,
)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def event(**overrides: object) -> StoredEvent:
    values: dict[str, object] = {
        "position": 1,
        "event_id": UUID(int=1),
        "stream_type": "synthetic_run",
        "stream_id": "run-hash",
        "stream_version": 1,
        "event_type": "SyntheticRunRequested",
        "event_schema_version": 1,
        "public_payload": {"a": 1, "b": {"x": True, "y": None}},
        "internal_payload": {"trace": [1, 2]},
        "secret_ref": None,
        "owner_user_id": "user-1",
        "team_id": None,
        "correlation_id": UUID(int=2),
        "causation_id": UUID(int=3),
        "occurred_at": NOW,
        "prev_hash": "0" * 64,
        "event_hash": "0" * 64,
    }
    values.update(overrides)
    provisional = StoredEvent.model_validate(values)
    return provisional.model_copy(update={"event_hash": calculate_event_hash(provisional)})


def test_event_hash_is_independent_of_json_key_order() -> None:
    first = event()
    reordered = event(
        public_payload={"b": {"y": None, "x": True}, "a": 1},
        internal_payload={"trace": [1, 2]},
    )

    assert first.event_hash == reordered.event_hash


def test_verify_stream_accepts_a_valid_chain() -> None:
    first = event()
    second = event(
        position=2,
        event_id=UUID(int=4),
        stream_version=2,
        event_type="SyntheticRunStarted",
        prev_hash=first.event_hash,
    )

    verify_stream((first, second))


def test_verify_stream_rejects_owner_scope_changes() -> None:
    first = event()
    second = event(
        position=2,
        event_id=UUID(int=4),
        stream_version=2,
        event_type="SyntheticRunStarted",
        owner_user_id="user-2",
        prev_hash=first.event_hash,
    )

    with pytest.raises(CorruptEventStreamError, match="owner scope changed"):
        verify_stream((first, second))


@pytest.mark.parametrize(
    "tampered",
    [
        event(public_payload={"changed": True}),
        event(prev_hash="f" * 64),
    ],
)
def test_verify_stream_rejects_the_first_corrupt_event(
    tampered: StoredEvent,
) -> None:
    valid = event()
    corrupt = tampered.model_copy(update={"event_hash": valid.event_hash})

    with pytest.raises(CorruptEventStreamError) as exc_info:
        verify_stream((corrupt,))

    assert exc_info.value.stream_version == 1


def test_verify_event_hashes_supports_global_position_reads_and_rejects_tamper() -> None:
    first_stream = event()
    second_stream = event(
        position=2,
        event_id=UUID(int=5),
        stream_id="another-run",
    )
    verify_event_hashes((first_stream, second_stream))

    corrupt = second_stream.model_copy(update={"public_payload": {"tampered": True}})
    with pytest.raises(CorruptEventStreamError, match="event hash mismatch"):
        verify_event_hashes((first_stream, corrupt))
