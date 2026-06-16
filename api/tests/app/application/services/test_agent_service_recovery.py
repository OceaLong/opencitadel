#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from app.application.services.agent_service import AgentService
from app.domain.models.event import AssistantNoticeEvent
from app.domain.models.session import Session, SessionStatus
from app.infrastructure.external.task.task_state import TaskStatus


class _EmptyOutputStream:
    async def get(self, start_id: str = "0", block_ms: int = 0):
        return None, None


class _FakeTask:
    id = "task-1"
    output_stream = _EmptyOutputStream()


async def _test_stale_stream_yields_recovery_notice():
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.session.list_events = AsyncMock(return_value=[])
    uow.session.update_unread_message_count = AsyncMock()
    uow.session.get_metadata = AsyncMock(
        return_value=Session(id="sess-1", task_id="task-1", status=SessionStatus.RUNNING),
    )

    task_state = MagicMock()
    task_state.get_output_seq_cursor = AsyncMock(return_value=None)
    task_state.get_runtime_snapshot = AsyncMock(
        return_value={
            "cancelled": False,
            "is_done": False,
            "status": TaskStatus.RUNNING,
            "meta": {"last_heartbeat_at": time.time() - 3600},
            "last_heartbeat_at": time.time() - 3600,
        },
    )

    service = AgentService(
        uow_factory=lambda: uow,
        task_cls=MagicMock(),
        checkpoint_service=MagicMock(),
        task_state_port=task_state,
        event_sequence_port=MagicMock(),
    )

    with patch("app.application.services.agent_service._STREAM_STALE_IDLE_SECONDS", 0):
        stream = service._consume_output_stream(_FakeTask(), "sess-1", None)
        event = await stream.__anext__()

    assert isinstance(event, AssistantNoticeEvent)


def test_stale_stream_yields_recovery_notice():
    asyncio.run(_test_stale_stream_yields_recovery_notice())
