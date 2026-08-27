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
