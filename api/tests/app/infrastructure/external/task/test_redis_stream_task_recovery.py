#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock

from app.domain.external.task import RecoverableTaskInputUnavailable
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.external.task.task_state import TaskStatus


async def _test_recoverable_input_gap_does_not_mark_done():
    runner = AsyncMock()
    runner.invoke = AsyncMock(side_effect=RecoverableTaskInputUnavailable("missing input"))
    runner.on_done = AsyncMock()

    task_state = AsyncMock()
    task_state.set_status = AsyncMock()
    task_state.get_status = AsyncMock(return_value=TaskStatus.PENDING)

    task = RedisStreamTask(
        task_id="task-1",
        session_id="sess-1",
        task_runner=runner,
        task_state=task_state,
    )

    await task._execute_task()

    task_state.set_status.assert_awaited_with("task-1", TaskStatus.PENDING)
    assert ("task-1", TaskStatus.DONE) not in [
        call.args for call in task_state.set_status.await_args_list
    ]


def test_recoverable_input_gap_does_not_mark_done():
    asyncio.run(_test_recoverable_input_gap_does_not_mark_done())
