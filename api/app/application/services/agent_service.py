#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
from datetime import datetime
from typing import AsyncGenerator, Optional, List, Type, Callable

from pydantic import TypeAdapter

from app.application.services.llm_model_service import LLMModelService
from app.application.services.memory_service import MemoryService
from app.application.services.skill_service import SkillService
from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.external.task import Task
from app.domain.models.app_config import AgentConfig, MCPConfig, A2AConfig
from app.domain.models.event import BaseEvent, ErrorEvent, MessageEvent, Event, DoneEvent, WaitEvent
from app.domain.models.session import Session, SessionStatus
from app.domain.models.skill import Skill
from app.domain.repositories.uow import IUnitOfWork
from app.application.services.memory_extractor_service import MemoryExtractorService
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.services.tools.memory import MemoryTool
from app.infrastructure.external.llm.factory import LLMFactory

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
    ) -> None:
        self._uow_factory = uow_factory
        self._uow = uow_factory()
        self._llm_model_service = llm_model_service
        self._skill_service = skill_service
        self._memory_service = memory_service
        self._agent_config = agent_config
        self._mcp_config = mcp_config
        self._a2a_config = a2a_config
        self._sandbox_cls = sandbox_cls
        self._task_cls = task_cls
        self._json_parser = json_parser
        self._search_engine = search_engine
        self._file_storage = file_storage
        self._auto_extract_memory = auto_extract_memory
        logger.info("AgentService初始化成功")

    def _apply_skill_agent_params(self, agent_config: AgentConfig, skill: Skill) -> AgentConfig:
        """将 Skill agent_params 合并到 AgentConfig（唯一入口）"""
        params = skill.agent_params
        if not params:
            return agent_config
        overrides = {}
        if params.max_iterations is not None:
            overrides["max_iterations"] = params.max_iterations
        if params.max_retries is not None:
            overrides["max_retries"] = params.max_retries
        if params.max_search_results is not None:
            overrides["max_search_results"] = params.max_search_results
        return agent_config.model_copy(update=overrides) if overrides else agent_config

    async def _resolve_llm_and_config(self, session: Session):
        """解析会话级模型、Skill与长期记忆"""
        model_id = session.model_id
        skill = None
        skill_prompt = ""
        agent_config = self._agent_config
        temperature_override: Optional[float] = None

        if session.skill_id:
            try:
                skill = await self._skill_service.get_skill(session.skill_id)
                if skill.enabled:
                    skill_prompt = skill.system_prompt
                    agent_config = self._apply_skill_agent_params(agent_config, skill)
                    if skill.agent_params and skill.agent_params.temperature_override is not None:
                        temperature_override = skill.agent_params.temperature_override
                    if not model_id and skill.recommended_model_id:
                        model_id = skill.recommended_model_id
                else:
                    skill = None
            except Exception:
                skill = None

        llm_model = await self._llm_model_service.resolve_model(model_id)
        if temperature_override is not None:
            llm_model = llm_model.model_copy(update={"temperature": temperature_override})
        llm = LLMFactory.create(llm_model, thinking_enabled=session.thinking_enabled)
        long_term_memory_block = await self._memory_recall(session.id)
        return llm, agent_config, skill, skill_prompt, long_term_memory_block

    async def _memory_recall(self, session_id: str) -> str:
        try:
            return await self._memory_service.recall_for_session(session_id)
        except Exception as e:
            logger.warning(f"召回长期记忆失败: {e}")
            return ""

    async def _get_task(self, session: Session) -> Optional[Task]:
        task_id = session.task_id
        if not task_id:
            return None
        return self._task_cls.get(task_id)

    async def _create_task(self, session: Session) -> Task:
        sandbox = None
        sandbox_id = session.sandbox_id
        if sandbox_id:
            sandbox = await self._sandbox_cls.get(sandbox_id)
        if not sandbox:
            sandbox = await self._sandbox_cls.create()
            session.sandbox_id = sandbox.id
            async with self._uow:
                await self._uow.session.save(session)

        llm, agent_config, skill, skill_prompt, ltm_block = await self._resolve_llm_and_config(session)

        browser = await sandbox.get_browser(supports_multimodal=llm.supports_multimodal)
        if not browser:
            logger.error(f"获取沙箱[{sandbox.id}]中的浏览器实例失败")
            raise RuntimeError(f"获取沙箱[{sandbox.id}]中的浏览器实例失败")

        async def save_memory_fn(title, content, tags, scope):
            entry = await self._memory_service.save_from_tool(
                title=title, content=content, tags=tags, scope=scope, session_id=session.id
            )
            return {"id": entry.id}

        extra_tools = [MemoryTool(save_fn=save_memory_fn, session_id=session.id)]

        async def on_complete(session_id: str) -> None:
            if self._auto_extract_memory:
                extractor = MemoryExtractorService(
                    uow_factory=self._uow_factory,
                    llm=llm,
                    json_parser=self._json_parser,
                )
                await extractor.extract_from_session(session_id)

        task_runner = AgentTaskRunner(
            uow_factory=self._uow_factory,
            llm=llm,
            agent_config=agent_config,
            mcp_config=self._mcp_config,
            a2a_config=self._a2a_config,
            session_id=session.id,
            file_storage=self._file_storage,
            json_parser=self._json_parser,
            browser=browser,
            search_engine=self._search_engine,
            sandbox=sandbox,
            skill=skill,
            skill_prompt=skill_prompt,
            long_term_memory_block=ltm_block,
            extra_tools=extra_tools,
            on_complete_callback=on_complete if self._auto_extract_memory else None,
        )

        task = self._task_cls.create(task_runner=task_runner)
        session.task_id = task.id
        async with self._uow:
            await self._uow.session.save(session)
        return task

    async def _safe_update_unread_count(self, session_id: str) -> None:
        try:
            uow = self._uow_factory()
            async with uow:
                await uow.session.update_unread_message_count(session_id, 0)
        except Exception as e:
            logger.warning(f"会话[{session_id}]后台更新未读消息计数失败: {e}")

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
    ) -> AsyncGenerator[BaseEvent, None]:
        session_missing = False
        try:
            async with self._uow:
                session = await self._uow.session.get_by_id(session_id)
            if not session:
                logger.error(f"尝试与不存在的任务会话[{session_id}]对话")
                session_missing = True
                yield ErrorEvent(error=_SESSION_NOT_FOUND_MSG)
                return

            if model_id is not None or skill_id is not None or thinking_enabled is not None:
                async with self._uow:
                    await self._uow.session.update_session_config(
                        session_id,
                        model_id=model_id,
                        skill_id=skill_id,
                        thinking_enabled=thinking_enabled,
                        clear_model=model_id == "",
                        clear_skill=skill_id == "",
                    )
                    session = await self._uow.session.get_by_id(session_id)

            task = await self._get_task(session)

            if message:
                if session.status != SessionStatus.RUNNING or task is None:
                    task = await self._create_task(session)
                    if not task:
                        logger.error(f"会话[{session_id}]创建任务失败")
                        raise RuntimeError(f"会话[{session_id}]创建任务失败")

                async with self._uow:
                    await self._uow.session.update_latest_message(
                        session_id=session_id,
                        message=message,
                        timestamp=timestamp or datetime.now(),
                    )

                async with self._uow:
                    db_attachments = [await self._uow.file.get_by_id(id) for id in (attachments or [])]

                message_event = MessageEvent(
                    role="user",
                    message=message,
                    attachments=[a for a in db_attachments if a is not None],
                )
                event_id = await task.input_stream.put(message_event.model_dump_json())
                message_event.id = event_id
                yield message_event
                async with self._uow:
                    await self._uow.session.add_event(session_id, message_event)
                await task.invoke()
                logger.info(f"往会话[{session_id}]输入消息队列写入消息: {message[:50]}...")

            logger.info(f"会话[{session_id}]已启动")

            while task and not task.done:
                event_id, event_str = await task.output_stream.get(start_id=latest_event_id, block_ms=0)
                latest_event_id = event_id
                if event_str is None:
                    continue
                event = TypeAdapter(Event).validate_json(event_str)
                event.id = event_id
                async with self._uow:
                    await self._uow.session.update_unread_message_count(session_id, 0)
                yield event
                if isinstance(event, (DoneEvent, ErrorEvent, WaitEvent)):
                    break

            logger.info(f"会话[{session_id}]本轮运行结束")
        except Exception as e:
            logger.exception(f"任务会话[{session_id}]对话出错: {str(e)}")
            event = ErrorEvent(error=str(e))
            try:
                async with self._uow:
                    await self._uow.session.add_event(session_id, event)
            except (asyncio.CancelledError, Exception) as add_err:
                logger.warning(f"会话[{session_id}]添加错误事件失败: {add_err}")
            yield event
        finally:
            if not session_missing:
                try:
                    asyncio.create_task(self._safe_update_unread_count(session_id))
                except RuntimeError:
                    logger.warning(f"会话[{session_id}]无法创建后台任务更新未读消息计数")

    async def stop_session(self, session_id: str) -> None:
        async with self._uow:
            session = await self._uow.session.get_by_id(session_id)
        if not session:
            raise RuntimeError("任务会话不存在, 请核实后重试")
        task = await self._get_task(session)
        if task:
            task.cancel()
        async with self._uow:
            await self._uow.session.update_status(session_id, SessionStatus.COMPLETED)

    async def shutdown(self) -> None:
        logger.info("正在清除所有会话任务资源并释放")
        await self._task_cls.destroy()
        logger.info("所有会话任务资源清除成功")
