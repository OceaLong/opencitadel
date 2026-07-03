#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Prepare session/task state before recoverable worker retry."""
from __future__ import annotations

import logging
from typing import Callable, Optional, Type

from app.domain.models.error_codes import TASK_INFRA_FAILED
from app.domain.models.event import MessageEvent
from app.domain.models.session import SessionStatus
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.checkpoint_service import CheckpointService
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask

logger = logging.getLogger(__name__)

RECOVERABLE_ERROR_CODES = frozenset({TASK_INFRA_FAILED})


async def requeue_latest_user_message(
        task: RedisStreamTask,
        session_id: str,
        uow_factory: Callable[[], IUnitOfWork],
) -> bool:
    try:
        if await task.input_stream.size() > 0:
            return True
        async with uow_factory() as uow:
            records = await uow.session.list_events(session_id, limit=500, latest=True)
        for _, event in reversed(records):
            if isinstance(event, MessageEvent) and event.role == "user" and event.message:
                await task.input_stream.put(event.model_dump_json())
                return True
        return False
    except Exception as exc:
        logger.warning(
            "恢复任务输入失败 session_id=%s task_id=%s: %s",
            session_id,
            task.id,
            exc,
        )
        return False


async def prepare_recoverable_retry(
        *,
        session_id: str,
        task_id: str,
        task_cls: Type[RedisStreamTask],
        uow_factory: Callable[[], IUnitOfWork],
        checkpoint_service: CheckpointService,
        error_code: Optional[str],
) -> None:
    if error_code not in RECOVERABLE_ERROR_CODES:
        return

    try:
        checkpoint = await checkpoint_service.resume_latest_checkpoint(session_id)
        if checkpoint:
            logger.info(
                "可恢复重试已还原 checkpoint: session_id=%s task_id=%s checkpoint_id=%s",
                session_id,
                task_id,
                checkpoint.id,
            )
    except Exception as exc:
        logger.warning(
            "可恢复重试 checkpoint 还原失败: session_id=%s task_id=%s error=%s",
            session_id,
            task_id,
            exc,
        )

    async with uow_factory() as uow:
        await uow.session.update_status(session_id, SessionStatus.RUNNING)

    task = task_cls.from_task_id(task_id, session_id)
    requeued = await requeue_latest_user_message(task, session_id, uow_factory)
    if requeued:
        logger.info(
            "可恢复重试已写回用户输入: session_id=%s task_id=%s",
            session_id,
            task_id,
        )
    else:
        logger.warning(
            "可恢复重试未找到用户输入: session_id=%s task_id=%s",
            session_id,
            task_id,
        )
