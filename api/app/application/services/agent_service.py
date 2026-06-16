#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
import json
from datetime import datetime
from typing import AsyncGenerator, Optional, List, Type, Callable

from pydantic import TypeAdapter

from app.domain.external.event_sequence import EventSequencePort
from app.domain.external.task import Task
from app.domain.external.task_state_port import TaskStatePort
from app.domain.models.checkpoint import Checkpoint
from app.domain.models.codebase import SessionMode
from app.domain.models.event import (
    BaseEvent,
    ErrorEvent,
    MessageEvent,
    Event,
    DoneEvent,
    SessionStatusEvent,
)
from app.domain.models.event_policy import TRANSIENT_EVENT_TYPES
from app.domain.models.event_upgrader import upgrade_event_payload
from app.domain.models.session import Session, SessionStatus
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.checkpoint_service import CheckpointService
from app.infrastructure.external.task.task_state import TaskStatus

logger = logging.getLogger(__name__)

_SESSION_NOT_FOUND_MSG = "任务会话不存在, 请核实后重试"
_TERMINAL_SESSION_STATUSES = {"waiting", "completed", "cancelled", "failed"}


class AgentService:
    """Manus智能体服务"""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            task_cls: Type[Task],
            checkpoint_service: CheckpointService,
            task_state_port: TaskStatePort,
            event_sequence_port: EventSequencePort,
    ) -> None:
        self._uow_factory = uow_factory
        self._task_cls = task_cls
        self._task_state = task_state_port
        self._event_sequence = event_sequence_port
        self._checkpoint_service = checkpoint_service
        logger.info("AgentService初始化成功")

    async def _get_task(self, session: Session) -> Optional[Task]:
        task_id = session.task_id
        if not task_id:
            return None
        return await self._task_cls.get(task_id)

    async def _task_is_terminal(self, task: Task) -> bool:
        snapshot = await self._task_state.get_runtime_snapshot(task.id)
        return bool(snapshot.get("cancelled") or snapshot.get("is_done"))

    async def _cleanup_task_resources(self, task_id: Optional[str]) -> None:
        if not task_id:
            return
        cleanup = getattr(self._task_cls, "destroy_task_resources", None)
        if cleanup is None:
            return
        try:
            await cleanup(task_id)
        except Exception as exc:
            logger.warning("清理旧任务 Redis 资源失败 task_id=%s: %s", task_id, exc)

    async def _create_task(self, session: Session) -> Task:
        previous_task_id = session.task_id
        task = await self._task_cls.create_for_session(session.id)
        session.task_id = task.id
        async with self._uow_factory() as uow:
            await uow.session.save(session)
            await uow.session.update_status(session.id, SessionStatus.RUNNING)
        await self._cleanup_task_resources(previous_task_id)
        return task

    async def _safe_update_unread_count(self, session_id: str) -> None:
        try:
            uow = self._uow_factory()
            async with uow:
                await uow.session.update_unread_message_count(session_id, 0)
        except Exception as e:
            logger.warning(f"会话[{session_id}]后台更新未读消息计数失败: {e}")

    async def _resolve_last_event_seq(
            self,
            session_id: str,
            latest_event_id: Optional[str],
    ) -> int:
        if not latest_event_id:
            return 0
        try:
            return int(latest_event_id)
        except (TypeError, ValueError):
            pass
        try:
            async with self._uow_factory() as uow:
                resolved = await uow.session.get_event_seq_by_stream_id(
                    session_id,
                    latest_event_id,
                )
            return int(resolved or 0)
        except Exception:
            return 0

    async def _resolve_redis_cursor(self, task_id: str, last_seq: int) -> str:
        if last_seq <= 0:
            return "0"
        cursor = await self._task_state.get_output_seq_cursor(task_id, last_seq)
        return cursor or "0"

    async def _consume_output_stream(
            self,
            task: Task,
            session_id: str,
            latest_event_id: Optional[str],
    ) -> AsyncGenerator[BaseEvent, None]:
        output_stream = task.output_stream
        task_id = task.id
        last_seq = await self._resolve_last_event_seq(session_id, latest_event_id)
        redis_cursor = await self._resolve_redis_cursor(task_id, last_seq)
        await self._safe_update_unread_count(session_id)

        terminal_statuses = {
            TaskStatus.DONE,
            TaskStatus.CANCELLED,
            TaskStatus.FAILED,
        }

        def is_terminal_status_event(event: BaseEvent) -> bool:
            return (
                isinstance(event, SessionStatusEvent)
                and event.status in _TERMINAL_SESSION_STATUSES
            )

        async def replay_persisted_events():
            nonlocal last_seq
            async with self._uow_factory() as uow:
                records = await uow.session.list_events(session_id, after=last_seq, limit=500)
            for seq, event in records:
                if seq <= last_seq:
                    continue
                last_seq = seq
                event.id = str(seq)
                yield event

        async def current_terminal_status_event() -> Optional[SessionStatusEvent]:
            async with self._uow_factory() as uow:
                current_session = await uow.session.get_metadata(session_id)
            if current_session and current_session.status.value in _TERMINAL_SESSION_STATUSES:
                return SessionStatusEvent(status=current_session.status.value)
            return None

        if last_seq > 0 and redis_cursor == "0":
            async for persisted_event in replay_persisted_events():
                yield persisted_event
                if is_terminal_status_event(persisted_event):
                    return

        while True:
            stream_message_id, event_str = await output_stream.get(
                start_id=redis_cursor,
                block_ms=500,
            )
            if event_str is not None:
                redis_cursor = stream_message_id
                event_payload = json.loads(event_str)
                event = TypeAdapter(Event).validate_python(upgrade_event_payload(event_payload))
                if event.type in TRANSIENT_EVENT_TYPES:
                    yield event
                    continue
                try:
                    event_seq = int(event.id)
                except (TypeError, ValueError):
                    continue
                if event_seq <= last_seq:
                    continue
                last_seq = event_seq
                event.id = str(event_seq)
                yield event
                if is_terminal_status_event(event):
                    return
                continue

            snapshot = await self._task_state.get_runtime_snapshot(task_id)
            if snapshot.get("cancelled"):
                yield SessionStatusEvent(status="cancelled")
                return
            status = snapshot.get("status")
            if snapshot.get("is_done") and status in terminal_statuses:
                async for persisted_event in replay_persisted_events():
                    yield persisted_event
                    if is_terminal_status_event(persisted_event):
                        return
                terminal_event = await current_terminal_status_event()
                if terminal_event:
                    yield terminal_event
                return

    async def chat(
            self,
            session_id: str,
            message: Optional[str] = None,
            attachments: Optional[List[str]] = None,
            latest_event_id: Optional[str] = None,
            timestamp: Optional[datetime] = None,
            model_id: Optional[str] = None,
            skill_id: Optional[str] = None,
            thinking_enabled: Optional[bool] = None,
            mode: Optional[SessionMode] = None,
    ) -> AsyncGenerator[BaseEvent, None]:
        session_missing = False
        try:
            async with self._uow_factory() as uow:
                session = await uow.session.get_by_id(session_id)
            if not session:
                logger.error(f"尝试与不存在的任务会话[{session_id}]对话")
                session_missing = True
                yield ErrorEvent(error=_SESSION_NOT_FOUND_MSG)
                return

            if (
                model_id is not None
                or skill_id is not None
                or thinking_enabled is not None
                or mode is not None
            ):
                async with self._uow_factory() as uow:
                    await uow.session.update_session_config(
                        session_id,
                        model_id=model_id,
                        skill_id=skill_id,
                        thinking_enabled=thinking_enabled,
                        clear_model=model_id == "",
                        clear_skill=skill_id == "",
                    )
                    if mode is not None:
                        session = await uow.session.get_by_id(session_id)
                        if session:
                            session.mode = mode
                            await uow.session.save(session)
                    session = await uow.session.get_by_id(session_id)

            task = await self._get_task(session)

            if message:
                if (
                    session.status != SessionStatus.RUNNING
                    or task is None
                    or await self._task_is_terminal(task)
                ):
                    task = await self._create_task(session)
                    if not task:
                        logger.error(f"会话[{session_id}]创建任务失败")
                        raise RuntimeError(f"会话[{session_id}]创建任务失败")

                message_event = MessageEvent(
                    role="user",
                    message=message,
                )
                seq = await self._event_sequence.allocate()
                message_event.id = str(seq)
                async with self._uow_factory() as uow:
                    await uow.session.update_latest_message(
                        session_id=session_id,
                        message=message,
                        timestamp=timestamp or datetime.now(),
                    )
                    db_attachments = await uow.file.list_by_ids(attachments or [])
                    message_event.attachments = db_attachments
                    await uow.session.add_event(session_id, message_event, seq=seq)
                await task.input_stream.put(message_event.model_dump_json())
                yield message_event
                await task.invoke()
                logger.info(f"往会话[{session_id}]输入消息队列写入消息: {message[:50]}...")

            if not task:
                task = await self._get_task(session)
            if not task:
                return

            logger.info(f"会话[{session_id}]已启动, task_id={task.id}")

            async for event in self._consume_output_stream(task, session_id, latest_event_id):
                yield event

            logger.info(f"会话[{session_id}]本轮运行结束")
        except Exception as e:
            logger.exception(f"任务会话[{session_id}]对话出错: {str(e)}")
            event = ErrorEvent(error=str(e))
            try:
                seq = await self._event_sequence.allocate()
                event.id = str(seq)
                async with self._uow_factory() as uow:
                    await uow.session.add_event(session_id, event, seq=seq)
            except (asyncio.CancelledError, Exception) as add_err:
                logger.warning(f"会话[{session_id}]添加错误事件失败: {add_err}")
            yield event
        finally:
            if not session_missing:
                try:
                    asyncio.create_task(self._safe_update_unread_count(session_id))
                except RuntimeError:
                    logger.warning(f"会话[{session_id}]无法创建后台任务更新未读消息计数")

    async def list_checkpoints(self, session_id: str) -> List[Checkpoint]:
        return await self._checkpoint_service.list_checkpoints(session_id)

    async def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> None:
        await self._checkpoint_service.restore(session_id, checkpoint_id)

    async def stop_session(self, session_id: str) -> None:
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id)
        if not session:
            raise RuntimeError("任务会话不存在, 请核实后重试")
        if session.task_id:
            await self._task_state.request_cancel(session.task_id)
        async with self._uow_factory() as uow:
            await uow.session.update_status(session_id, SessionStatus.CANCELLED)

    async def shutdown(self) -> None:
        logger.info("AgentService 关闭（任务由独立 worker 执行，无需清理本地 registry）")
