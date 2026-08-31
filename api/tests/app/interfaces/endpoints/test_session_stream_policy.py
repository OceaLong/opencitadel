import asyncio
from unittest.mock import AsyncMock

import pytest

from app.application.ports.coordination import RedisConnectivity
from app.application.ports.streams import HintPoll
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.domain.models.session import Session
from app.domain.runtime_policy import OperationsPolicy, TrafficPolicy
from app.interfaces.endpoints.session._helpers import session_stream_interval_seconds
from app.interfaces.endpoints.session_routes import stream_sessions
from tests.runtime_policy_support import MutablePolicyReader


@pytest.mark.asyncio
async def test_session_stream_wait_reads_current_operations_policy() -> None:
    reader = MutablePolicyReader(
        operations=OperationsPolicy(
            traffic=TrafficPolicy(session_stream_interval_seconds=3),
        )
    )

    assert await session_stream_interval_seconds(reader) == 3
    reader.set_operations(
        OperationsPolicy(
            traffic=TrafficPolicy(session_stream_interval_seconds=11),
        )
    )
    assert await session_stream_interval_seconds(reader) == 11
    assert [fresh for fresh, _now in reader.operations_calls] == [True, True]


@pytest.mark.asyncio
async def test_session_stream_heartbeat_reconciles_from_authoritative_service() -> None:
    reader = MutablePolicyReader(
        operations=OperationsPolicy(
            traffic=TrafficPolicy(session_stream_interval_seconds=1),
        )
    )
    service = AsyncMock()
    service.get_all_sessions = AsyncMock(
        return_value=[Session(id="session-1", title="authoritative")]
    )

    class _Stream:
        async def poll(self, *, timeout_seconds: float) -> HintPoll:
            assert timeout_seconds == 1
            return HintPoll(None, RedisConnectivity(True, None))

    class _Opened:
        async def __aenter__(self):
            return _Stream()

        async def __aexit__(self, *_args):
            return False

    streams = AsyncMock()
    streams.open = lambda: _Opened()
    response = await stream_sessions(
        limit=100,
        offset=0,
        ctx=WorkspaceContext(
            principal=Principal(user_id="user-1"),
            scope=OwnerScope.personal("user-1"),
        ),
        session_service=service,
        policy_reader=reader,
        streams=streams,
    )

    first = await anext(response.body_iterator)
    heartbeat = await anext(response.body_iterator)
    await response.body_iterator.aclose()

    assert first.event == "sessions"
    assert heartbeat.event == "sessions"
    assert service.get_all_sessions.await_count == 2


@pytest.mark.asyncio
async def test_session_disconnect_finishes_database_snapshot_before_cancelling() -> None:
    reader = MutablePolicyReader()
    query_started = asyncio.Event()
    release_query = asyncio.Event()
    query_cancelled = asyncio.Event()

    async def get_all_sessions(*_args, **_kwargs):
        query_started.set()
        try:
            await release_query.wait()
        except asyncio.CancelledError:
            query_cancelled.set()
            raise
        return []

    service = AsyncMock()
    service.get_all_sessions = AsyncMock(side_effect=get_all_sessions)
    streams = AsyncMock()
    response = await stream_sessions(
        limit=100,
        offset=0,
        ctx=WorkspaceContext(
            principal=Principal(user_id="user-1"),
            scope=OwnerScope.personal("user-1"),
        ),
        session_service=service,
        policy_reader=reader,
        streams=streams,
    )

    reading = asyncio.create_task(anext(response.body_iterator))
    await asyncio.wait_for(query_started.wait(), timeout=1)
    reading.cancel()
    await asyncio.sleep(0)

    assert not reading.done()
    assert not query_cancelled.is_set()

    release_query.set()
    with pytest.raises(asyncio.CancelledError):
        await reading

    assert not query_cancelled.is_set()
    streams.open.assert_not_called()
