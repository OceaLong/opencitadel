#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
import json
from datetime import datetime
from typing import AsyncGenerator, Optional, List, Type, Callable

from pydantic import TypeAdapter

from app.application.services.llm_model_service import LLMModelService
from app.application.services.memory_service import MemoryService
from app.application.services.skill_service import SkillService
from app.application.services.config_provider import AppConfigProvider, get_app_config_provider
from app.application.services.task_runner_factory import TaskRunnerFactory
from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.external.task import Task
from app.domain.models.app_config import AgentConfig, MCPConfig, A2AConfig
from app.domain.models.event import BaseEvent, ErrorEvent, MessageEvent, Event, DoneEvent, WaitEvent
from app.domain.models.event_upgrader import upgrade_event_payload
from app.domain.models.checkpoint import Checkpoint
from app.domain.models.codebase import SessionMode
from app.domain.models.session import Session, SessionStatus
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.checkpoint_service import CheckpointService
from app.infrastructure.external.message_queue.redis_stream_message_queue import RedisStreamMessageQueue
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.external.task.task_state import get_task_state

logger = logging.getLogger(__name__)

_SESSION_NOT_FOUND_MSG = "任务会话不存在, 请核实后重试"


class AgentService:
    """Manus智能体服务"""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            llm_model_service: LLMModelService,
            skill_service: SkillService,
            memory_service: MemoryService,
            agent_config: AgentConfig,
            mcp_config: MCPConfig,
            a2a_config: A2AConfig,
            sandbox_cls: Type[Sandbox],
            task_cls: Type[Task],
            json_parser: JSONParser,
            search_engine: SearchEngine,
            file_storage: FileStorage,
            auto_extract_memory: bool = True,
            config_provider: Optional[AppConfigProvider] = None,
            checkpoint_service: Optional[CheckpointService] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._task_cls = task_cls
        self._task_state = get_task_state()
        self._checkpoint_service = checkpoint_service
        self._runner_factory = TaskRunnerFactory(
            uow_factory=uow_factory,
            llm_model_service=llm_model_service,
            skill_service=skill_service,
            memory_service=memory_service,
            agent_config=agent_config,
            mcp_config=mcp_config,
            a2a_config=a2a_config,
            sandbox_cls=sandbox_cls,
            json_parser=json_parser,
            search_engine=search_engine,
            file_storage=file_storage,
            auto_extract_memory=auto_extract_memory,
            config_provider=config_provider or get_app_config_provider(),
            checkpoint_service=checkpoint_service,
        )
        logger.info("AgentService初始化成功")

    async def _get_task(self, session: Session) -> Optional[RedisStreamTask]:
        task_id = session.task_id
        if not task_id:
            return None
        return await RedisStreamTask.get(task_id)

    async def _create_task(self, session: Session) -> RedisStreamTask:
        task = await RedisStreamTask.create_for_session(session.id)
        session.task_id = task.id
        async with self._uow_factory() as uow:
            await uow.session.save(session)
            await uow.session.update_status(session.id, SessionStatus.RUNNING)
        return task

    async def _safe_update_unread_count(self, session_id: str) -> None:
        try:
            uow = self._uow_factory()
            async with uow:
                await uow.session.update_unread_message_count(session_id, 0)
        except Exception as e:
            logger.warning(f"会话[{session_id}]后台更新未读消息计数失败: {e}")

    async def _consume_output_stream(
            self,
            task_id: str,
            session_id: str,
            latest_event_id: Optional[str],
    ) -> AsyncGenerator[BaseEvent, None]:
        output_stream = RedisStreamMessageQueue(f"task:output:{task_id}")
        cursor = latest_event_id or "0"
        await self._safe_update_unread_count(session_id)

        while True:
            if await self._task_state.is_cancelled(task_id):
                yield DoneEvent()
                break

            event_id, event_str = await output_stream.get(start_id=cursor, block_ms=100)
            if event_str is not None:
                cursor = event_id
                event_payload = json.loads(event_str)
                event = TypeAdapter(Event).validate_python(upgrade_event_payload(event_payload))
                event.id = event_id
                yield event
                if isinstance(event, (DoneEvent, ErrorEvent, WaitEvent)):
                    return
                continue

            if await self._task_state.is_done(task_id):
                async with self._uow_factory() as uow:
                    session = await uow.session.get_by_id(session_id)
                if session and session.status in {
                    SessionStatus.COMPLETED,
                    SessionStatus.WAITING,
                    SessionStatus.CANCELLED,
                }:
                    return
                if await output_stream.is_empty():
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
                should_dispatch = False
                if session.status != SessionStatus.RUNNING or task is None:
                    task = await self._create_task(session)
                    should_dispatch = True
                    if not task:
                        logger.error(f"会话[{session_id}]创建任务失败")
                        raise RuntimeError(f"会话[{session_id}]创建任务失败")

                async with self._uow_factory() as uow:
                    await uow.session.update_latest_message(
                        session_id=session_id,
                        message=message,
                        timestamp=timestamp or datetime.now(),
                    )

                async with self._uow_factory() as uow:
                    db_attachments = await uow.file.list_by_ids(attachments or [])

                message_event = MessageEvent(
                    role="user",
                    message=message,
                    attachments=db_attachments,
                )
                event_id = await task.input_stream.put(message_event.model_dump_json())
                message_event.id = event_id
                yield message_event
                async with self._uow_factory() as uow:
                    await uow.session.add_event(session_id, message_event)
                if should_dispatch:
                    await task.invoke()
                logger.info(f"往会话[{session_id}]输入消息队列写入消息: {message[:50]}...")

            if not task:
                task = await self._get_task(session)
            if not task:
                return

            logger.info(f"会话[{session_id}]已启动, task_id={task.id}")

            async for event in self._consume_output_stream(task.id, session_id, latest_event_id):
                yield event

            logger.info(f"会话[{session_id}]本轮运行结束")
        except Exception as e:
            logger.exception(f"任务会话[{session_id}]对话出错: {str(e)}")
            event = ErrorEvent(error=str(e))
            try:
                async with self._uow_factory() as uow:
                    await uow.session.add_event(session_id, event)
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
        if not self._checkpoint_service:
            return []
        return await self._checkpoint_service.list_checkpoints(session_id)

    async def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> None:
        if not self._checkpoint_service:
            raise RuntimeError("还原点服务未启用")
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
