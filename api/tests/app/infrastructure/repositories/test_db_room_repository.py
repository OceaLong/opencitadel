#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.room import Room, RoomStatus, TodMode
from app.infrastructure.repositories.db_room_repository import DBRoomRepository


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_save_room_flushes_after_insert():
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result

    repo = DBRoomRepository(db_session=session)
    room = Room(
        id="room-1",
        code="12345678",
        name="测试房间",
        host_participant_id="host-1",
        tod_mode=TodMode.RANDOM,
        turn_order=["host-1"],
        current_turn_index=0,
        status=RoomStatus.ACTIVE,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    await repo.save_room(room)

    session.add.assert_called_once()
    session.flush.assert_awaited_once()


@pytest.mark.anyio
async def test_save_room_update_does_not_flush():
    session = AsyncMock()
    record = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = record
    session.execute.return_value = result

    repo = DBRoomRepository(db_session=session)
    room = Room(
        id="room-1",
        code="12345678",
        name="更新房间",
        host_participant_id="host-1",
        tod_mode=TodMode.RANDOM,
        turn_order=["host-1"],
        current_turn_index=0,
        status=RoomStatus.ACTIVE,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    await repo.save_room(room)

    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    assert record.name == "更新房间"
