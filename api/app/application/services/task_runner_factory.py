#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Factory for building AgentTaskRunner instances (shared by API and worker)."""
import logging
from typing import Callable, Optional, Type

from app.application.services.config_provider import AppConfigProvider, get_app_config_provider

from app.application.services.llm_model_service import LLMModelService
from app.application.services.memory_extractor_service import MemoryExtractorService
from app.application.services.memory_service import MemoryService
from app.application.services.skill_service import SkillService
from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.models.app_config import AgentConfig, MCPConfig, A2AConfig
from app.domain.utils.app_config_filter import filter_a2a_config_by_refs, filter_mcp_config_by_refs
from app.domain.models.session import Session
from app.domain.models.skill import Skill
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.services.checkpoint_service import CheckpointService
from app.domain.services.tools.image_generation import ImageGenerationTool
from app.domain.services.tools.memory import MemoryTool
from app.infrastructure.external.llm.factory import LLMFactory

logger = logging.getLogger(__name__)


class TaskRunnerFactory:
    """Build AgentTaskRunner from session state."""

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
            json_parser: JSONParser,
            search_engine: SearchEngine,
            file_storage: FileStorage,
            auto_extract_memory: bool = True,
            config_provider: Optional[AppConfigProvider] = None,
            checkpoint_service: Optional[CheckpointService] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._llm_model_service = llm_model_service
        self._skill_service = skill_service
        self._memory_service = memory_service
        self._agent_config = agent_config
        self._mcp_config = mcp_config
        self._a2a_config = a2a_config
        self._sandbox_cls = sandbox_cls
        self._json_parser = json_parser
        self._search_engine = search_engine
        self._file_storage = file_storage
        self._auto_extract_memory = auto_extract_memory
        self._config_provider = config_provider or get_app_config_provider()
        self._checkpoint_service = checkpoint_service

    async def _refresh_runtime_config(self) -> None:
        app_config = await self._config_provider.get()
        self._agent_config = app_config.agent_config
        self._mcp_config = app_config.mcp_config
        self._a2a_config = app_config.a2a_config

    def _apply_skill_agent_params(self, agent_config: AgentConfig, skill: Skill) -> AgentConfig:
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
        return llm, agent_config, skill, skill_prompt, long_term_memory_block, llm_model

    async def _memory_recall(self, session_id: str) -> str:
        try:
            return await self._memory_service.recall_for_session(session_id)
        except Exception as e:
            logger.warning(f"召回长期记忆失败: {e}")
            return ""

    async def create_runner(self, session: Session) -> AgentTaskRunner:
        await self._refresh_runtime_config()
        sandbox = None
        sandbox_id = session.sandbox_id
        if sandbox_id:
            sandbox = await self._sandbox_cls.get(sandbox_id)
        if not sandbox:
            sandbox = await self._sandbox_cls.create()
            session.sandbox_id = sandbox.id
            async with self._uow_factory() as uow:
                await uow.session.save(session)

        llm, agent_config, skill, skill_prompt, ltm_block, llm_model = await self._resolve_llm_and_config(session)
        model_id = llm_model.id

        browser = await sandbox.get_browser(supports_multimodal=llm.supports_multimodal)
        if not browser:
            raise RuntimeError(f"获取沙箱[{sandbox.id}]中的浏览器实例失败")

        async def save_memory_fn(title, content, tags, scope):
            entry = await self._memory_service.save_from_tool(
                title=title, content=content, tags=tags, scope=scope, session_id=session.id
            )
            return {"id": entry.id}

        extra_tools = [MemoryTool(save_fn=save_memory_fn, session_id=session.id)]
        caps = llm_model.capabilities
        if caps and caps.image_generation:
            extra_tools.append(
                ImageGenerationTool(
                    llm=llm,
                    llm_model=llm_model,
                    file_storage=self._file_storage,
                )
            )

        mcp_config = filter_mcp_config_by_refs(
            self._mcp_config,
            skill.mcp_server_refs if skill else None,
        )
        a2a_config = filter_a2a_config_by_refs(
            self._a2a_config,
            skill.a2a_server_refs if skill else None,
        )

        async def on_complete(session_id: str) -> None:
            if self._auto_extract_memory:
                extractor = MemoryExtractorService(
                    uow_factory=self._uow_factory,
                    llm=llm,
                    json_parser=self._json_parser,
                )
                await extractor.extract_from_session(session_id)

        return AgentTaskRunner(
            uow_factory=self._uow_factory,
            llm=llm,
            agent_config=agent_config,
            mcp_config=mcp_config,
            a2a_config=a2a_config,
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
            model_id=model_id,
            checkpoint_service=self._checkpoint_service,
        )
