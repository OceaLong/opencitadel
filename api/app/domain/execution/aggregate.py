"""Pure aggregate decisions and deterministic replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.domain.execution.activity import ActivityRequest
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.events import NewEvent, StoredEvent
from app.domain.execution.registry import CommandRegistry, EventRegistry
from app.domain.execution.serialization import canonical_state_hash
from app.domain.execution.store import ZERO_HASH
from app.domain.execution.timer import ScheduledCommandRequest

StateT = TypeVar("StateT", bound=BaseModel)


@dataclass(frozen=True)
class Decision:
    events: tuple[NewEvent, ...]
    activity_requests: tuple[ActivityRequest, ...] = ()
    scheduled_commands: tuple[ScheduledCommandRequest, ...] = ()


class Aggregate(Protocol[StateT]):
    state_type: type[StateT]
    snapshot_serializer_version: int
    command_registry: CommandRegistry
    event_registry: EventRegistry

    def initial_state(self, stream_id: str) -> StateT: ...

    def evolve(self, state: StateT, event: StoredEvent) -> StateT: ...

    def decide(self, state: StateT, command: CommandEnvelope) -> Decision: ...


@dataclass(frozen=True)
class ReplaySnapshot[SnapshotStateT: BaseModel]:
    stream_id: str
    stream_version: int
    state: SnapshotStateT
    state_hash: str
    last_event_hash: str


@dataclass(frozen=True)
class ReplayResult[ResultStateT: BaseModel]:
    state: ResultStateT
    stream_version: int
    state_hash: str
    last_event_hash: str


def replay[ReplayStateT: BaseModel](
    aggregate: Aggregate[ReplayStateT],
    events: tuple[StoredEvent, ...] | list[StoredEvent],
    snapshot: ReplaySnapshot[ReplayStateT] | None = None,
    *,
    stream_id: str | None = None,
) -> ReplayResult[ReplayStateT]:
    if snapshot is not None:
        if canonical_state_hash(snapshot.state) != snapshot.state_hash:
            raise ValueError("snapshot state hash does not match its state")
        resolved_stream_id = snapshot.stream_id
        state = snapshot.state
        version = snapshot.stream_version
        last_event_hash = snapshot.last_event_hash
    else:
        resolved_stream_id = stream_id or (events[0].stream_id if events else None)
        if resolved_stream_id is None:
            raise ValueError("stream_id is required to replay an empty stream")
        state = aggregate.initial_state(resolved_stream_id)
        version = 0
        last_event_hash = ZERO_HASH

    if stream_id is not None and stream_id != resolved_stream_id:
        raise ValueError("replay stream_id does not match snapshot stream")

    for event in events:
        if event.stream_id != resolved_stream_id:
            raise ValueError("event belongs to a different stream")
        if event.stream_version != version + 1:
            raise ValueError("event stream versions must be contiguous")
        state = aggregate.evolve(state, event)
        version = event.stream_version
        last_event_hash = event.event_hash

    return ReplayResult(
        state=state,
        stream_version=version,
        state_hash=canonical_state_hash(state),
        last_event_hash=last_event_hash,
    )


__all__ = [
    "Aggregate",
    "Decision",
    "ReplayResult",
    "ReplaySnapshot",
    "replay",
]
