from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.interfaces.endpoints.scheduling_routes import notification_stream


@pytest.mark.asyncio
async def test_notification_reconnect_reads_persisted_unread_before_streaming() -> None:
    service = AsyncMock()
    service.list_for_user = AsyncMock(return_value=[object(), object()])
    streams = AsyncMock()
    response = await notification_stream(
        ctx=WorkspaceContext(
            principal=Principal(user_id="user-1"),
            scope=OwnerScope.personal("user-1"),
        ),
        service=service,
        streams=streams,
    )

    connected = await anext(response.body_iterator)
    await response.body_iterator.aclose()

    assert connected.event == "connected"
    assert json.loads(connected.data) == {"user_id": "user-1", "unread_count": 2}
    service.list_for_user.assert_awaited_once_with("user-1", unread_only=True)
    streams.open.assert_not_called()


@pytest.mark.asyncio
async def test_notification_disconnect_finishes_database_snapshot_before_cancelling() -> None:
    query_started = asyncio.Event()
    release_query = asyncio.Event()
    query_cancelled = asyncio.Event()

    async def list_for_user(*_args, **_kwargs):
        query_started.set()
        try:
            await release_query.wait()
        except asyncio.CancelledError:
            query_cancelled.set()
            raise
        return []

    service = AsyncMock()
    service.list_for_user = AsyncMock(side_effect=list_for_user)
    streams = AsyncMock()
    response = await notification_stream(
        ctx=WorkspaceContext(
            principal=Principal(user_id="user-1"),
            scope=OwnerScope.personal("user-1"),
        ),
        service=service,
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
