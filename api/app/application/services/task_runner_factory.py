#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Factory for building AgentTaskRunner instances (shared by API and worker)."""
import logging
from typing import Callable, Optional, Type

from app.application.services.audit_service import AuditService
from app.application.services.codebase_service import CodebaseService
from app.application.services.artifact_service import ArtifactService
from app.application.services.config_provider import AppConfigProvider, get_runtime_config
from app.application.services.llm_model_service import LLMModelService
from app.application.services.memory_extractor_service import MemoryExtractorService
from app.application.services.notification_service import NotificationService
from app.application.services.scheduled_job_service import ScheduledJobService
from app.application.services.memory_service import MemoryService
from app.application.services.skill_service import SkillService
from app.domain.external.connection_pool import A2AConnectionPoolPort, MCPConnectionPoolPort
from app.domain.external.event_sequence import EventSequencePort
from app.domain.external.file_storage import FileStorage
from app.domain.external.object_storage import ObjectStoragePort
from app.domain.external.json_parser import JSONParser
from app.domain.external.observability import ObservabilityPort
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.external.session_state import SessionStatePort
from app.domain.external.task_state_port import TaskStatePort
from app.domain.models.agent_runtime_settings import AgentMemoryRuntimeSettings, AgentRuntimeSettings
from app.domain.models.app_config import AgentConfig, MCPConfig, A2AConfig
from app.domain.models.codebase import SessionMode
from app.domain.models.session import Session, SessionStatus
from app.domain.models.skill import Skill
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.services.agent.sandbox_provider import LazyBrowser, LazySandbox, SandboxProvider
from app.domain.services.checkpoint_service import CheckpointService
from app.domain.services.tools.codebase_tools import CodebaseTool
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.image_generation import ImageGenerationTool
from app.domain.services.tools.knowledge_base_tools import KnowledgeBaseTool
from app.domain.services.tools.artifact import ArtifactTool
from app.domain.services.tools.memory import MemoryTool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.subagent_factory import build_subagent_tool
from app.domain.utils.app_config_filter import filter_a2a_config_by_refs, filter_mcp_config_by_refs
from app.infrastructure.external.llm.factory import LLMFactory
from app.infrastructure.external.llm.resilient_llm import ModelUnavailableError, create_resilient_llm
from app.domain.models.error_codes import MODEL_UNAVAILABLE

logger = logging.getLogger(__name__)

CODE_AGENT_SKILL_PROMPT = """
你是代码改造 Agent。用户已上传并索引了一个代码库，你可以：
1. 使用 codebase 工具检索、理解代码
2. 使用 file/shell 工具在沙箱工作区修改代码
3. 修改前通过澄清步骤确认需求细节
4. 每次修改后说明变更的文件与行号
工作目录为代码库沙箱路径，请在该目录下进行所有改码操作。
"""

DOC_AGENT_SKILL_PROMPT = """
你是企业文档知识库 Agent。用户已上传并索引了企业文档知识库，你可以：
1. 使用 knowledge_base 工具检索、理解文档内容
2. 结合 file/shell/browser 等工具生成报告、摘要、对比分析等交付物
3. 引用知识库内容时必须标注文档来源，优先保留 `kbdoc://` 引用链接
4. 不要编造知识库中没有依据的事实
"""


class TaskRunnerFactory:
    """Build AgentTaskRunner from session state."""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            llm_model_service: LLMModelService,
            skill_service: SkillService,
            memory_service: MemoryService,
            sandbox_cls: Type[Sandbox],
            json_parser: JSONParser,
            search_engine: SearchEngine,
            file_storage: FileStorage,
            config_provider: AppConfigProvider,
            checkpoint_service: CheckpointService,
            task_state_port: TaskStatePort,
            observability_port: ObservabilityPort,
            event_sequence_port: EventSequencePort,
            session_state_factory: Callable[[], SessionStatePort],
            mcp_connection_pool: MCPConnectionPoolPort,
            a2a_connection_pool: A2AConnectionPoolPort,
            artifact_service: Optional[ArtifactService] = None,
            audit_service: Optional[AuditService] = None,
            codebase_service: Optional[CodebaseService] = None,
            object_storage: Optional[ObjectStoragePort] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._llm_model_service = llm_model_service
        self._skill_service = skill_service
        self._memory_service = memory_service
        self._sandbox_cls = sandbox_cls
        self._json_parser = json_parser
        self._search_engine = search_engine
        self._file_storage = file_storage
        self._config_provider = config_provider
        self._checkpoint_service = checkpoint_service
        self._task_state_port = task_state_port
        self._observability_port = observability_port
        self._event_sequence_port = event_sequence_port
        self._session_state_factory = session_state_factory
        self._mcp_connection_pool = mcp_connection_pool
        self._a2a_connection_pool = a2a_connection_pool
        self._artifact_service = artifact_service
        self._audit_service = audit_service
        self._codebase_service = codebase_service
        self._object_storage = object_storage
        self._agent_config = AgentConfig()
        self._mcp_config = MCPConfig()
        self._a2a_config = A2AConfig()
        self._auto_extract_memory = True

    async def _refresh_runtime_config(self) -> None:
        app_config = await self._config_provider.get()
        self._agent_config = app_config.agent_config
        self._mcp_config = app_config.mcp_config
        self._a2a_config = app_config.a2a_config
        self._auto_extract_memory = app_config.memory.auto_extract_enabled

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
        llm = create_resilient_llm(
            llm_model,
            thinking_enabled=session.thinking_enabled,
            llm_model_service=self._llm_model_service,
        )
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

        llm, agent_config, skill, skill_prompt, ltm_block, llm_model = await self._resolve_llm_and_config(session)
        model_id = llm_model.id

        on_ready = None
        if (
            session.codebase_id
            and session.mode == SessionMode.AGENT
            and self._codebase_service
            and self._object_storage
        ):
            codebase_id = session.codebase_id
            codebase_service = self._codebase_service
            object_storage = self._object_storage

            async def ensure_codebase_attached(sandbox: Sandbox) -> None:
                try:
                    await codebase_service.attach_to_session_sandbox(
                        codebase_id,
                        sandbox,
                        object_storage,
                    )
                except Exception as exc:
                    logger.warning(
                        "代码库物化到会话沙箱失败 session=%s: %s",
                        session.id,
                        exc,
                    )

            on_ready = ensure_codebase_attached

        sandbox_provider = SandboxProvider(
            session_id=session.id,
            sandbox_id=session.sandbox_id,
            sandbox_cls=self._sandbox_cls,
            uow_factory=self._uow_factory,
            on_ready=on_ready,
        )
        sandbox = LazySandbox(sandbox_provider)
        browser = LazyBrowser(
            sandbox_provider,
            supports_multimodal=llm.supports_multimodal,
            llm=llm,
        )

        async def save_memory_fn(title, content, tags, scope):
            entry = await self._memory_service.save_from_tool(
                title=title, content=content, tags=tags, scope=scope, session_id=session.id
            )
            return {"id": entry.id}

        extra_tools = [MemoryTool(save_fn=save_memory_fn, session_id=session.id)]

        runtime = get_runtime_config()
        if runtime.feature_flags.enable_artifacts and self._artifact_service:
            artifact_service = self._artifact_service

            async def write_artifact(**kwargs):
                artifact, event = await artifact_service.write_content(
                    session_id=session.id,
                    artifact_id=kwargs.get("artifact_id"),
                    kind=kwargs["kind"],
                    title=kwargs["title"],
                    content=kwargs["content"],
                )
                return artifact.model_dump(mode="json"), event

            async def finalize_artifact(artifact_id: str):
                artifact, event = await artifact_service.finalize(session.id, artifact_id)
                return artifact.model_dump(mode="json"), event

            extra_tools.append(ArtifactTool(write_fn=write_artifact, finalize_fn=finalize_artifact))
            skill_prompt = (
                f"{skill_prompt}\n\n交付物规则：产出最终文档或网页时必须调用 artifact_write / artifact_finalize，"
                f"不要仅用 write_file 写入交付物。"
            ).strip() if skill_prompt else (
                "交付物规则：产出最终文档或网页时必须调用 artifact_write / artifact_finalize。"
            )

        codebase_prompt = ""
        if session.codebase_id:
            async with self._uow_factory() as uow:
                codebase = await uow.codebase.get_by_id(session.codebase_id)
            if codebase:
                if session.mode == SessionMode.AGENT:
                    codebase_prompt = CODE_AGENT_SKILL_PROMPT
                extra_tools.append(
                    CodebaseTool(
                        uow_factory=self._uow_factory,
                        codebase_id=codebase.id,
                        sandbox=sandbox,
                        workspace_path=codebase.workspace_path,
                    )
                )
        if codebase_prompt:
            skill_prompt = f"{skill_prompt}\n\n{codebase_prompt}".strip() if skill_prompt else codebase_prompt
        knowledge_base_prompt = ""
        if session.knowledge_base_id:
            async with self._uow_factory() as uow:
                kb = await uow.knowledge_base.get_kb(session.knowledge_base_id)
            if kb:
                if session.mode == SessionMode.AGENT:
                    knowledge_base_prompt = DOC_AGENT_SKILL_PROMPT
                extra_tools.append(
                    KnowledgeBaseTool(
                        uow_factory=self._uow_factory,
                        kb_id=kb.id,
                        llm=llm,
                    )
                )
        if knowledge_base_prompt:
            skill_prompt = (
                f"{skill_prompt}\n\n{knowledge_base_prompt}".strip()
                if skill_prompt
                else knowledge_base_prompt
            )
        caps = llm_model.capabilities
        if caps and caps.image_generation:
            extra_tools.append(
                ImageGenerationTool(
                    llm=llm,
                    llm_model=llm_model,
                    file_storage=self._file_storage,
                    owner_user_id=session.owner_user_id,
                    team_id=session.team_id,
                )
            )

        runtime = get_runtime_config()
        runtime_settings = AgentMemoryRuntimeSettings(
            compact_tool_content_max_chars=runtime.memory.compact_tool_content_max_chars,
            compact_strategy=runtime.memory.compact_strategy,
            compact_token_threshold=runtime.memory.compact_token_threshold,
            compact_keep_recent=runtime.memory.compact_keep_recent,
            compact_always_on_step_boundary=runtime.memory.compact_always_on_step_boundary,
            compact_rule_trigger_threshold=runtime.memory.compact_rule_trigger_threshold,
            tool_output_offload_enabled=runtime.memory.tool_output_offload_enabled,
            tool_output_offload_threshold_chars=runtime.memory.tool_output_offload_threshold_chars,
        )
        agent_runtime_settings = AgentRuntimeSettings(
            tool_timeout_seconds=runtime.worker.tool_timeout_seconds,
            tool_gate_call_level_enabled=(
                skill.agent_params.tool_gate_call_level_enabled
                if skill and skill.agent_params
                else None
            ),
            memory=runtime_settings,
        )

        import asyncio
        stateful_tool_lock = asyncio.Lock()
        allowed_for_subagent = skill.allowed_tools if (skill and skill.allowed_tools) else None
        subagent_tool = build_subagent_tool(
            uow_factory=self._uow_factory,
            session_id=session.id,
            llm=llm,
            agent_config=agent_config,
            json_parser=self._json_parser,
            browser=browser,
            sandbox=sandbox,
            search_engine=self._search_engine,
            mcp_tool=MCPTool(self._mcp_connection_pool),
            a2a_tool=A2ATool(self._a2a_connection_pool),
            observability_port=self._observability_port,
            runtime_settings=agent_runtime_settings,
            extra_tools=extra_tools,
            skill_prompt=skill_prompt,
            long_term_memory_block=ltm_block,
            allowed_tool_names=allowed_for_subagent,
            model_id=model_id,
            file_storage=self._file_storage,
            stateful_tool_lock=stateful_tool_lock,
        )
        extra_tools = list(extra_tools) + [subagent_tool]

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

        async def on_session_terminal(session_id: str, status) -> None:
            app_config = await self._config_provider.get()
            job_service = ScheduledJobService(uow_factory=self._uow_factory)
            notification_service = NotificationService(uow_factory=self._uow_factory)
            await job_service.on_session_terminal(
                session_id,
                status.value if hasattr(status, "value") else str(status),
                notification_service=notification_service,
                mcp_pool=self._mcp_connection_pool,
                app_config=app_config,
            )
            if (
                status == SessionStatus.COMPLETED
                and self._audit_service
                and self._artifact_service
            ):
                async with self._uow_factory() as uow:
                    session_row = await uow.session.get_by_id(session_id)
                if session_row and session_row.operator_scope:
                    try:
                        report = await self._audit_service.build_session_audit_report(session_id)
                        await self._artifact_service.write_content(
                            session_id=session_id,
                            artifact_id=None,
                            kind="doc",
                            title="会话审计报告",
                            content=report,
                        )
                    except Exception as exc:
                        logger.warning("生成会话审计 artifact 失败 session=%s: %s", session_id, exc)

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
            sandbox_provider=sandbox_provider,
            skill=skill,
            skill_prompt=skill_prompt,
            long_term_memory_block=ltm_block,
            extra_tools=extra_tools,
            on_complete_callback=on_complete if self._auto_extract_memory else None,
            on_session_terminal_callback=on_session_terminal,
            model_id=model_id,
            checkpoint_service=self._checkpoint_service,
            mode=session.mode,
            codebase_id=session.codebase_id,
            knowledge_base_id=session.knowledge_base_id,
            task_state_port=self._task_state_port,
            observability_port=self._observability_port,
            event_sequence_port=self._event_sequence_port,
            session_state_port=self._session_state_factory(),
            runtime_settings=agent_runtime_settings,
            mcp_connection_pool=self._mcp_connection_pool,
            a2a_connection_pool=self._a2a_connection_pool,
            stateful_tool_lock=stateful_tool_lock,
            owner_user_id=session.owner_user_id,
            team_id=session.team_id,
        )
