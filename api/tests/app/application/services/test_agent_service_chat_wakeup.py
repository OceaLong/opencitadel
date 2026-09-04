"""Chat SSE wake-up behaviour: catch-up, Redis hint-driven re-query, fallback
poll, and clean teardown on cancellation.

These exercise the streaming tail of ``AgentService.chat`` in *resume* mode
(``message=None``), which skips admission and drives the event loop directly so
the wake-up mechanics can be observed in isolation.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.application.execution.public_projection import (
    PublicEventPage,
    PublicExecutionEvent,
)
from app.application.ports.coordination import RedisConnectivity
from app.application.ports.streams import WakeupBatch
from app.application.services.agent_service import AgentService
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session

_AVAILABLE = RedisConnectivity(True, None)
_UNAVAILABLE = RedisConnectivity(False, "redis_unavailable")


def _event(cursor: str, event_type: str, **payload: object) -> PublicExecutionEvent:
    return PublicExecutionEvent(
        cursor=cursor,
        event_id=uuid4(),
        event_type=event_type,
        run_id=UUID("10000000-0000-0000-0000-000000000001"),
        stream_id="session-1",
        stream_version=1,
        payload=dict(payload),
        occurred_at=datetime.now(UTC),
    )


class _SessionRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def get_by_id(self, session_id, scope=None):
        return self._session if session_id == self._session.id else None


class _UnitOfWork:
    def __init__(self, session: Session) -> None:
        self.session = _SessionRepo(session)
        self.skill = SimpleNamespace(get_by_id=self._none)
        self.file = SimpleNamespace(get_by_id=self._none)

    async def _none(self, *_args, **_kwargs):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class _ScriptedProjection:
    """Returns queued pages on successive incremental (``after=``) queries.

    Once the script is exhausted every further query is empty, so the loop
    keeps waiting on the wake-up port until an idle timeout or a terminal
    event ends it.
    """

    def __init__(self, pages: list[tuple[PublicExecutionEvent, ...]]) -> None:
        self._pages = list(pages)
        self.query_count = 0

    async def list_events(self, *, latest: bool = False, after=None, **_):
        if latest:
            return PublicEventPage(events=(), next_cursor=None, prev_cursor=None, has_earlier=False)
        self.query_count += 1
        events = self._pages.pop(0) if self._pages else ()
        return PublicEventPage(events=events, next_cursor=None, prev_cursor=None, has_earlier=False)


class _FakeWakeup:
    """Scripted ``WakeupPort``. Each script step is one of:

    ``"hint"``   – a wake-up carrying an ``execution.events`` message.
    ``"timeout"``– fallback timeout, empty batch, Redis available.
    ``"down"``   – Redis unavailable (returns immediately, empty).
    ``"block"``  – blocks forever (until the generator is cancelled/closed).
    """

    def __init__(self, script: list[str]) -> None:
        self._script = list(script)
        self.reads: list[tuple[str, int]] = []

    async def read_broadcast(self, cursor: str, *, block_milliseconds: int) -> WakeupBatch:
        self.reads.append((cursor, block_milliseconds))
        step = self._script.pop(0) if self._script else "timeout"
        if step == "block":
            await asyncio.sleep(3600)
        next_cursor = f"{cursor}-{len(self.reads)}"
        if step == "hint":
            from app.application.ports.execution import WakeupMessage

            message = WakeupMessage(
                destination="execution.events",
                dedupe_key="event:1",
                event_position=1,
            )
            return WakeupBatch(next_cursor, (message,), _AVAILABLE)
        if step == "down":
            return WakeupBatch(cursor, (), _UNAVAILABLE)
        return WakeupBatch(next_cursor, (), _AVAILABLE)


def _service(projection, wakeup=None) -> AgentService:
    session = Session(id="session-1", owner_user_id="user-1")
    return AgentService(
        uow_factory=lambda: _UnitOfWork(session),
        admission_service=SimpleNamespace(),
        command_ingress=SimpleNamespace(),
        public_projection=projection,
        run_projection=SimpleNamespace(),
        events_wakeup=wakeup,
        poll_interval_seconds=0.001,
        idle_timeout_seconds=5.0,
        fallback_poll_seconds=0.05,
    )


async def _resume(service: AgentService) -> list[PublicExecutionEvent]:
    return [
        event
        async for event in service.chat(
            "session-1",
            owner_scope=OwnerScope.personal("user-1"),
            latest_event_id="c0",
        )
    ]


@pytest.mark.asyncio
async def test_catchup_pushes_already_persisted_events_without_waiting():
    projection = _ScriptedProjection(
        [(_event("c1", "message", role="assistant", message="hi"), _event("c2", "done"))]
    )
    wakeup = _FakeWakeup([])
    events = await _resume(_service(projection, wakeup))

    assert [e.event_type for e in events] == ["message", "done"]
    # Terminal reached on the catch-up query: the wake-up stream is never read.
    assert wakeup.reads == []


@pytest.mark.asyncio
async def test_hint_triggers_incremental_query_and_push():
    # Catch-up empty -> wait -> hint -> increment (assistant) -> terminal.
    projection = _ScriptedProjection(
        [
            (),
            (_event("c1", "message", role="assistant", message="hi"),),
            (_event("c2", "done"),),
        ]
    )
    wakeup = _FakeWakeup(["hint"])
    events = await _resume(_service(projection, wakeup))

    assert [e.event_type for e in events] == ["message", "done"]
    assert len(wakeup.reads) == 1
    # Blocks with the long fallback budget, not a tight poll.
    assert wakeup.reads[0][1] == pytest.approx(50)


@pytest.mark.asyncio
async def test_fallback_timeout_requeries_when_no_hint_arrives():
    # No hint ever fires; the fallback timeout must still re-query and deliver.
    projection = _ScriptedProjection([(), (), (_event("c1", "done"),)])
    wakeup = _FakeWakeup(["timeout", "timeout"])
    events = await _resume(_service(projection, wakeup))

    assert [e.event_type for e in events] == ["done"]
    assert len(wakeup.reads) == 2


@pytest.mark.asyncio
async def test_redis_unavailable_degrades_without_hot_looping():
    # A single unavailable read must pace (short sleep) rather than spin, and a
    # subsequent recovery still delivers the terminal event.
    projection = _ScriptedProjection([(), (), (_event("c1", "done"),)])
    wakeup = _FakeWakeup(["down", "timeout"])
    events = await _resume(_service(projection, wakeup))

    assert [e.event_type for e in events] == ["done"]
    # First read (down) returns the same cursor; second read reuses it.
    assert wakeup.reads[1][0] == wakeup.reads[0][0]


@pytest.mark.asyncio
async def test_generator_close_cleans_up_without_extra_reads():
    # Catch-up yields one event, then the loop blocks on the wake-up read.
    projection = _ScriptedProjection([(_event("c1", "message", role="assistant"),)])
    wakeup = _FakeWakeup(["block"])
    agen = _service(projection, wakeup).chat(
        "session-1",
        owner_scope=OwnerScope.personal("user-1"),
        latest_event_id="c0",
    )

    first = await agen.__anext__()
    assert first.event_type == "message"

    # Force the loop into the blocking read, then cancel the consumer the way
    # sse-starlette does when the client disconnects: the CancelledError unwinds
    # the generator out of the blocked read.
    pump = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0.02)
    assert len(wakeup.reads) == 1  # blocked inside read
    pump.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pump

    await asyncio.sleep(0.02)
    # No further reads issued after teardown; the stateless stream needs no
    # explicit unsubscribe, and the generator is now finished.
    assert len(wakeup.reads) == 1
    await agen.aclose()


@pytest.mark.asyncio
async def test_without_wakeup_port_uses_legacy_fixed_poll():
    # No wake-up port injected: the loop must fall back to the fixed-interval
    # poll and still deliver events pushed after an empty catch-up.
    projection = _ScriptedProjection([(), (_event("c1", "done"),)])
    events = await _resume(_service(projection, wakeup=None))

    assert [e.event_type for e in events] == ["done"]
    assert projection.query_count == 2
