from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict

from app.domain.execution.aggregate import (
    Aggregate,
    Decision,
    ReplaySnapshot,
    replay,
)
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.events import NewEvent, StoredEvent
from app.domain.execution.serialization import canonical_state_hash

NOW = datetime(2026, 8, 20, 11, 0, tzinfo=UTC)


class CounterState(BaseModel):
    model_config = ConfigDict(frozen=True)

    stream_id: str
    value: int = 0
    stream_version: int = 0


@dataclass(frozen=True)
class CounterAggregate(Aggregate[CounterState]):
    def initial_state(self, stream_id: str) -> CounterState:
        return CounterState(stream_id=stream_id)

    def evolve(self, state: CounterState, event: StoredEvent) -> CounterState:
        return state.model_copy(
            update={
                "value": state.value + int(event.public_payload["amount"]),
                "stream_version": event.stream_version,
            }
        )

    def decide(
        self,
        state: CounterState,
        command: CommandEnvelope,
    ) -> Decision:
        return Decision(
            events=(
                NewEvent(
                    event_type="CounterIncremented",
                    event_schema_version=1,
                    public_payload={"amount": command.payload["amount"]},
                    internal_payload={},
                ),
            )
        )


def stored_event(version: int, amount: int = 1) -> StoredEvent:
    return StoredEvent(
        position=version,
        event_id=UUID(int=version),
        stream_type="counter",
        stream_id="counter-1",
        stream_version=version,
        event_type="CounterIncremented",
        event_schema_version=1,
        public_payload={"amount": amount},
        internal_payload={},
        secret_ref=None,
        owner_user_id="user-1",
        team_id=None,
        correlation_id=UUID(int=100),
        causation_id=UUID(int=200 + version),
        occurred_at=NOW,
        prev_hash="0" * 64,
        event_hash=f"{version:064x}",
    )


def test_full_replay_matches_snapshot_plus_tail() -> None:
    aggregate = CounterAggregate()
    events = tuple(stored_event(version) for version in range(1, 6))
    prefix = replay(aggregate, events[:3])
    snapshot = ReplaySnapshot(
        stream_id="counter-1",
        stream_version=prefix.stream_version,
        state=prefix.state,
        state_hash=prefix.state_hash,
        last_event_hash=prefix.last_event_hash,
    )

    full = replay(aggregate, events)
    resumed = replay(aggregate, events[3:], snapshot=snapshot)

    assert full.state == resumed.state
    assert full.state_hash == resumed.state_hash
    assert resumed.stream_version == 5


def test_replay_rejects_non_contiguous_stream_versions() -> None:
    aggregate = CounterAggregate()

    with pytest.raises(ValueError, match="contiguous"):
        replay(aggregate, (stored_event(1), stored_event(3)))


def test_canonical_hash_is_key_order_independent_and_normalizes_utc() -> None:
    class TimedState(BaseModel):
        at: datetime
        metadata: dict[str, int]

    utc_state = TimedState(at=NOW, metadata={"a": 1, "b": 2})
    offset_state = TimedState(
        at=NOW.astimezone(timezone(timedelta(hours=8))),
        metadata={"b": 2, "a": 1},
    )

    assert canonical_state_hash(utc_state) == canonical_state_hash(offset_state)


def test_snapshot_hash_mismatch_is_rejected() -> None:
    aggregate = CounterAggregate()
    state = aggregate.initial_state("counter-1")
    snapshot = ReplaySnapshot(
        stream_id="counter-1",
        stream_version=0,
        state=state,
        state_hash="not-the-state-hash",
        last_event_hash="0" * 64,
    )

    with pytest.raises(ValueError, match="snapshot state hash"):
        replay(aggregate, (), snapshot=snapshot)


def test_decision_defaults_to_no_external_work() -> None:
    decision = Decision(events=())

    assert decision.activity_requests == ()
    assert decision.scheduled_commands == ()
