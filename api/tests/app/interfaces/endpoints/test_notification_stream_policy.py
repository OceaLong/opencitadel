from __future__ import annotations

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
