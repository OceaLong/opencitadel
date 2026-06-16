#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.application.services.session_service import SessionService
from app.domain.models.session import Session


async def _run_delete_session_cancels_and_waits_for_task():
    session = Session(id="sess-1", task_id="task-1", sandbox_id=None)
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.session.get_by_id = AsyncMock(return_value=session)
    uow.session.delete_by_id = AsyncMock()

    task_state = MagicMock()
    task_state.request_cancel = AsyncMock()
    task_state.get_runtime_snapshot = AsyncMock(return_value={"is_done": True})

    service = SessionService(
        uow_factory=lambda: uow,
        sandbox_cls=MagicMock(),
        task_state_port=task_state,
    )
    await service.delete_session("sess-1")

    task_state.request_cancel.assert_awaited_once_with("task-1")
    task_state.get_runtime_snapshot.assert_awaited()
    uow.session.delete_by_id.assert_awaited_once_with("sess-1")


def test_delete_session_cancels_and_waits_for_task():
    asyncio.run(_run_delete_session_cancels_and_waits_for_task())
