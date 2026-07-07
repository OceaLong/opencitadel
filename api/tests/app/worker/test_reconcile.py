#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.models.session import Session, SessionStatus
from app.infrastructure.external.task.task_state import TaskStatus
from app.worker.main import AgentWorker


async def _test_reconcile_skips_fresh_heartbeat():
    session = Session(id="sess-1", task_id="task-1", status=SessionStatus.RUNNING)
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.session.list_recoverable_running = AsyncMock(return_value=[session])

    task_state = MagicMock()
    task_state.get_runtime_snapshot = AsyncMock(
        return_value={
            "is_done": False,
            "status": TaskStatus.RUNNING,
            "meta": {"last_heartbeat_at": time.time()},
        },
    )
    task_state.heartbeat_is_stale = MagicMock(return_value=False)
    task_state.dispatch = AsyncMock()

    worker = object.__new__(AgentWorker)
    worker._task_state = task_state
    worker._checkpoint_service = MagicMock()

    with patch("app.worker.main.get_uow", return_value=uow):
        await AgentWorker._reconcile_orphaned_tasks(worker, "test")

    task_state.dispatch.assert_not_awaited()


def test_reconcile_skips_fresh_heartbeat():
    asyncio.run(_test_reconcile_skips_fresh_heartbeat())
