#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.services.recoverable_task_retry import (
    RECOVERABLE_ERROR_CODES,
    prepare_recoverable_retry,
    requeue_latest_user_message,
)
from app.domain.models.error_codes import MODEL_UNAVAILABLE, TASK_INFRA_FAILED
from app.domain.models.event import MessageEvent
from app.domain.models.session import SessionStatus
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask


@pytest.mark.asyncio
async def test_recoverable_error_codes_contains_task_infra_failed():
    assert TASK_INFRA_FAILED in RECOVERABLE_ERROR_CODES
    assert MODEL_UNAVAILABLE not in RECOVERABLE_ERROR_CODES


@pytest.mark.asyncio
async def test_prepare_recoverable_retry_skips_non_recoverable_codes():
    checkpoint_service = MagicMock()
    checkpoint_service.resume_latest_checkpoint = AsyncMock()
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.session.update_status = AsyncMock()
    uow_factory = MagicMock(return_value=uow)

    await prepare_recoverable_retry(
        session_id="sess-1",
        task_id="task-1",
        task_cls=RedisStreamTask,
        uow_factory=uow_factory,
        checkpoint_service=checkpoint_service,
        error_code=MODEL_UNAVAILABLE,
    )

    checkpoint_service.resume_latest_checkpoint.assert_not_awaited()
    uow.session.update_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_recoverable_retry_resets_session_and_checkpoint():
    checkpoint = MagicMock()
    checkpoint.id = "cp-1"
    checkpoint_service = MagicMock()
    checkpoint_service.resume_latest_checkpoint = AsyncMock(return_value=checkpoint)
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.session.update_status = AsyncMock()
    uow.session.list_events = AsyncMock(return_value=[
        (1, MessageEvent(role="user", message="continue")),
    ])
    uow_factory = MagicMock(return_value=uow)

    task = RedisStreamTask(task_id="task-1", session_id="sess-1")
    task._input_stream = MagicMock()
    task._input_stream.size = AsyncMock(return_value=0)
    task._input_stream.put = AsyncMock()

    original_from_task_id = RedisStreamTask.from_task_id
    RedisStreamTask.from_task_id = classmethod(lambda cls, task_id, session_id="", task_state=None: task)

    try:
        await prepare_recoverable_retry(
            session_id="sess-1",
            task_id="task-1",
            task_cls=RedisStreamTask,
            uow_factory=uow_factory,
            checkpoint_service=checkpoint_service,
            error_code=TASK_INFRA_FAILED,
        )
    finally:
        RedisStreamTask.from_task_id = original_from_task_id

    checkpoint_service.resume_latest_checkpoint.assert_awaited_once_with("sess-1")
    uow.session.update_status.assert_awaited_once_with("sess-1", SessionStatus.RUNNING)
    task._input_stream.put.assert_awaited_once()


@pytest.mark.asyncio
async def test_requeue_latest_user_message_returns_true_when_stream_has_input():
    task = RedisStreamTask(task_id="task-1", session_id="sess-1")
    task._input_stream = MagicMock()
    task._input_stream.size = AsyncMock(return_value=1)

    ok = await requeue_latest_user_message(task, "sess-1", MagicMock())
    assert ok is True
