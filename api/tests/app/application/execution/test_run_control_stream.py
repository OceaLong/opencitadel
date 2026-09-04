"""Event-driven wakeup and idle-timeout semantics of the Run SSE stream."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.execution.public_projection import (
    PublicEventPage,
    PublicExecutionEvent,
)
from app.application.execution.run_control import RunControlService
from app.application.ports.coordination import RedisConnectivity
from app.application.ports.streams import WakeupBatch
from app.domain.models.scope import OwnerScope

NOW = datetime(2026, 9, 4, 9, tzinfo=UTC)
_AVAILABLE = RedisConnectivity(available=True)


def _event(cursor: str, event_type: str = "message") -> PublicExecutionEvent:
    return PublicExecutionEvent(
        cursor=cursor,
        event_id=uuid4(),
        event_type=event_type,
        run_id=None,
        stream_id="run-1",
        stream_version=1,
        payload={"type": event_type},
        occurred_at=NOW,
    )


class _Projection:
    """Empty first, then delivers pages as scripted."""

    def __init__(self, pages: list[tuple[PublicExecutionEvent, ...]]) -> None:
        self.pages = list(pages)
        self.queries = 0

    async def list_events(self, **kwargs):
        del kwargs
        self.queries += 1
        events = self.pages.pop(0) if self.pages else ()
        return PublicEventPage(
            events=events,
            next_cursor=None,
            prev_cursor=None,
            has_earlier=False,
        )


class _Hints:
    def __init__(self, wake_after_reads: int = 1) -> None:
        self.reads = 0
        self._wake_after = wake_after_reads

    async def read_broadcast(self, cursor: str, *, block_milliseconds: int):
        del block_milliseconds
        self.reads += 1
        if self.reads >= self._wake_after:
            return WakeupBatch(f"{self.reads}-0", (object(),), _AVAILABLE)  # type: ignore[arg-type]
        return WakeupBatch(cursor, (), _AVAILABLE)


def _service(projection: _Projection, hints: _Hints | None) -> RunControlService:
    return RunControlService(
        commands=object(),  # type: ignore[arg-type]  # stream path never submits
        run_projection=object(),  # type: ignore[arg-type]
        public_projection=projection,  # type: ignore[arg-type]
        events_wakeup=hints,
        poll_interval_seconds=0.01,
        idle_timeout_seconds=0.05,
    )


@pytest.mark.asyncio
async def test_hint_wakes_the_stream_and_terminal_event_closes_it() -> None:
    projection = _Projection([(), (_event("c1"), _event("c2", "RunCompleted"))])
    hints = _Hints(wake_after_reads=1)
    service = _service(projection, hints)

    events = [
        event
        async for event in service.stream_source(
            source_entity_type="session",
            source_entity_id="s-1",
            owner_scope=OwnerScope.personal("user-1"),
        )
    ]

    assert [event.event_type for event in events] == ["message", "RunCompleted"]
    # The empty first page parked the stream on the hint port, not a sleep.
    assert hints.reads >= 1
    assert projection.queries == 2


@pytest.mark.asyncio
async def test_idle_timeout_emits_an_explicit_stream_timeout_event() -> None:
    projection = _Projection([])
    service = _service(projection, hints=None)  # fallback: fixed-interval poll

    events = [
        event
        async for event in service.stream_source(
            source_entity_type="session",
            source_entity_id="s-1",
            owner_scope=OwnerScope.personal("user-1"),
        )
    ]

    assert len(events) == 1
    assert events[0].event_type == "stream_timeout"
    assert events[0].payload == {"type": "stream_timeout"}


@pytest.mark.asyncio
async def test_redis_outage_degrades_to_fixed_interval_polling() -> None:
    class _Down:
        async def read_broadcast(self, cursor: str, *, block_milliseconds: int):
            del block_milliseconds
            return WakeupBatch(
                cursor,
                (),
                RedisConnectivity(available=False, error_key="down"),
            )

    projection = _Projection([(), (_event("c1", "RunFailed"),)])
    service = _service(projection, _Down())  # type: ignore[arg-type]

    events = await asyncio.wait_for(
        _collect(service),
        timeout=2.0,
    )

    assert [event.event_type for event in events] == ["RunFailed"]


async def _collect(service: RunControlService):
    return [
        event
        async for event in service.stream_source(
            source_entity_type="session",
            source_entity_id="s-1",
            owner_scope=OwnerScope.personal("user-1"),
        )
    ]
