#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Factory for building AgentTaskRunner instances (shared by API and worker)."""
import logging
from typing import Callable, Optional, Type

from app.domain.errors import NotFoundError
from app.application.services.audit_service import AuditService
from app.application.services.codebase_service import CodebaseService
from app.application.services.artifact_service import ArtifactService
from app.application.services.config_provider import AppConfigProvider, get_runtime_config
from app.application.services.integration_server_service import A2AServerConfigService, MCPServerService
from app.application.services.llm_model_service import LLMModelService
from app.application.services.memory_extractor_service import MemoryExtractorService
from app.application.services.notification_service import NotificationService
from app.application.services.scheduled_job_service import ScheduledJobService
from app.application.services.patrol_run_service import PatrolRunService
# MCPPatrolCollectorValidator/MCPActuatorClient are no longer called directly
# here (moved into runner_bindings.patrol / runner_bindings.remediation) but
# stay imported so `patch("...task_runner_factory.MCPPatrolCollectorValidator...")`
# / `MCPActuatorClient...` in existing tests keep resolving — patching a
# classmethod through any module path that holds a reference to the same
# class object patches it globally, so this keeps those tests green without
# touching them.
from app.application.services.patrol_collector_validator import MCPPatrolCollectorValidator
from app.application.services.patrol_remediation_service import PatrolRemediationService
from app.application.services.memory_service import MemoryService
from app.application.services.runner_bindings.patrol import resolve_patrol_binding
from app.application.services.runner_bindings.remediation import exclude_actuator_server, resolve_remediation_binding
from app.application.services.runner_bindings.resource_authorizer import authorize_session_resources
from app.application.services.skill_service import SkillService
from app.domain.external.connection_pool import A2AConnectionPoolPort, MCPConnectionPoolPort
from app.domain.external.event_sequence import EventSequencePort
from app.domain.external.file_storage import FileStorage
from app.domain.external.object_storage import ObjectStoragePort
from app.domain.external.json_parser import JSONParser
from app.domain.external.observability import ObservabilityPort
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.external.task_state_port import TaskStatePort
from app.domain.models.agent_runtime_settings import AgentMemoryRuntimeSettings, AgentRuntimeSettings
from app.domain.models.app_config import AgentConfig, MCPConfig, A2AConfig, ModelResilienceConfig
from app.domain.models.codebase import Codebase, SessionMode
from app.domain.models.knowledge_base import KnowledgeBase
from app.domain.models.session import Session, SessionStatus
from app.domain.models.scope import OwnerScope
from app.domain.models.skill import Skill
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.services.agent.sandbox_provider import LazyBrowser, LazySandbox, SandboxProvider
from app.domain.services.checkpoint_service import CheckpointService
from app.domain.services.codebase.snapshot_service import VersionedCodeSource
from app.domain.services.tools.codebase_tools import CodebaseTool
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.image_generation import ImageGenerationTool
from app.domain.services.tools.knowledge_base_tools import KnowledgeBaseTool
from app.domain.services.tools.artifact import ArtifactTool
from app.domain.services.tools.memory import MemoryTool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.patrol import PatrolTool
from app.domain.services.tools.patrol_remediation import PatrolRemediationTool
from app.infrastructure.external.actuator_client import MCPActuatorClient
from app.domain.services.tools.capability_policy import CapabilityPolicy
from app.domain.services.session_flow_resolver import SessionFlowResolver
from app.domain.services.subagent_factory import build_subagent_tool
from app.domain.services.skills.skill_loader import render_active
from app.application.services.skill_recommender_service import SkillRecommenderService
from app.domain.services.prompts.loader import detect_locale_from_text
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

CODE_ASK_SKILL_PROMPT = """
当前会话已绑定代码库「{name}」，源码索引路径为 {workspace_path}。
请仅通过 codebase 工具的 semantic_search / read_code / find_symbol 等接口检索源码。
禁止通过 shell 或文件工具探索容器文件系统；/sandbox 是平台运行时目录，不是用户代码库。
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
            mcp_connection_pool: MCPConnectionPoolPort,
            a2a_connection_pool: A2AConnectionPoolPort,
            mcp_server_service: Optional[MCPServerService] = None,
            a2a_server_config_service: Optional[A2AServerConfigService] = None,
            artifact_service: Optional[ArtifactService] = None,
            audit_service: Optional[AuditService] = None,
            codebase_service: Optional[CodebaseService] = None,
            object_storage: Optional[ObjectStoragePort] = None,
            patrol_run_service: Optional[PatrolRunService] = None,
            patrol_remediation_service: Optional[PatrolRemediationService] = None,
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
        self._mcp_connection_pool = mcp_connection_pool
        self._a2a_connection_pool = a2a_connection_pool
        self._mcp_server_service = mcp_server_service
        self._a2a_server_config_service = a2a_server_config_service
        self._artifact_service = artifact_service
        self._audit_service = audit_service
        self._codebase_service = codebase_service
        self._object_storage = object_storage
        self._patrol_run_service = patrol_run_service
        self._patrol_remediation_service = patrol_remediation_service
        self._agent_config = AgentConfig()
        self._mcp_config = MCPConfig()
        self._a2a_config = A2AConfig()
        self._auto_extract_memory = True
        self._model_resilience = ModelResilienceConfig()

    @staticmethod
    def _scope_for_session(session: Session) -> OwnerScope:
        if not session.owner_user_id:
            raise NotFoundError("会话缺少所有者，拒绝加载关联资源")
        if session.team_id:
            return OwnerScope.team(session.owner_user_id, session.team_id)
        return OwnerScope.personal(session.owner_user_id)

    async def _authorize_session_resources(
            self,
            session: Session,
            scope: OwnerScope,
    ) -> tuple[Optional[Codebase], Optional[str], Optional[KnowledgeBase], Optional[str]]:
        # Thin delegate: logic lives in runner_bindings.resource_authorizer.
        # Kept as a bound method (rather than inlining the module-function
        # call at every call site) because several tests
        # (test_kb_version_invariants / test_task_runner_factory_kb_binding /
        # test_task_runner_factory_codebase_binding) call
        # `factory._authorize_session_resources(...)` directly.
        return await authorize_session_resources(self._uow_factory, session, scope)

    async def _build_versioned_code_source(
            self,
            codebase_id: str,
            version_id: str,
    ) -> VersionedCodeSource:
        if self._object_storage is None:
            raise NotFoundError("代码库快照存储不可用")
        async with self._uow_factory() as uow:
            version = await uow.codebase_version.get_version(
                version_id,
                codebase_id=codebase_id,
            )
        if (
            version is None
            or not version.source_snapshot_key
            or not version.source_digest
        ):
            raise NotFoundError("代码库版本快照不可用")
        return VersionedCodeSource(
            version_id=version.id,
            snapshot_key=version.source_snapshot_key,
            source_digest=version.source_digest,
            object_storage=self._object_storage,
        )

    async def _refresh_runtime_config(self, session: Optional[Session] = None) -> None:
        scope = self._scope_for_session(session) if session else None
        app_config = await self._config_provider.resolve_for_owner(scope)
        self._agent_config = app_config.agent_config
        self._auto_extract_memory = app_config.memory.auto_extract_enabled
        self._model_resilience = app_config.model_resilience
        if self._mcp_server_service is not None:
            self._mcp_config = await self._mcp_server_service.resolve_mcp_config(scope)
        else:
            self._mcp_config = app_config.mcp_config
        if self._a2a_server_config_service is not None:
            self._a2a_config = await self._a2a_server_config_service.resolve_a2a_config(scope)
        else:
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

    async def _resolve_llm_and_config(self, session: Session, latest_message: str = ""):
        scope = self._scope_for_session(session)
        model_id = session.model_id
        skill = None
        skill_prompt = ""
        agent_config = self._agent_config
        temperature_override: Optional[float] = None

        if not session.skill_id:
            await self._maybe_auto_recommend_skill(session, latest_message)

        if session.skill_id:
            try:
                skill = await self._skill_service.get_skill(session.skill_id, scope=scope)
                if skill.enabled:
                    skill_prompt = render_active(skill)
                    agent_config = self._apply_skill_agent_params(agent_config, skill)
                    if skill.agent_params and skill.agent_params.temperature_override is not None:
                        temperature_override = skill.agent_params.temperature_override
                    if not model_id and skill.recommended_model_id:
                        model_id = skill.recommended_model_id
                else:
                    skill = None
            except Exception:
                skill = None

        llm_model = await self._llm_model_service.resolve_model(model_id, scope=scope)
        if temperature_override is not None:
            llm_model = llm_model.model_copy(update={"temperature": temperature_override})
        llm = create_resilient_llm(
            llm_model,
            thinking_enabled=session.thinking_enabled,
            llm_model_service=self._llm_model_service,
            resilience_config=self._model_resilience,
        )
        long_term_memory_block = await self._memory_recall(session.id)
        return llm, agent_config, skill, skill_prompt, long_term_memory_block, llm_model

    async def _maybe_auto_recommend_skill(self, session: Session, latest_message: str = "") -> None:
        runtime = get_runtime_config()
        if not runtime.feature_flags.enable_skill_auto_recommend:
            return
        scope = self._scope_for_session(session)
        message = latest_message
        if not message:
            return
        skills = await self._skill_service.list_skills(enabled_only=True, scope=scope)
        llm_model = await self._llm_model_service.resolve_model(session.model_id, scope=scope)
        llm = create_resilient_llm(llm_model, llm_model_service=self._llm_model_service)
        recommender = SkillRecommenderService(llm, self._json_parser)
        result = await recommender.recommend(message, skills)
        if not result.skill_id:
            return
        async with self._uow_factory() as uow:
            await uow.session.update_session_config(session.id, skill_id=result.skill_id)
        session.skill_id = result.skill_id
        logger.info(
            "Auto-recommended skill session=%s skill_id=%s confidence=%s",
            session.id,
            result.skill_id,
            result.confidence,
        )

    async def _latest_user_message(self, session_id: str) -> str:
        async with self._uow_factory() as uow:
            records = await uow.session.list_events(session_id, limit=50)
        for _, event in reversed(records):
            role = getattr(event, "role", None)
            if role == "user" and getattr(event, "message", None):
                return str(event.message)
        return ""

    async def _memory_recall(self, session_id: str) -> str:
        try:
            return await self._memory_service.recall_for_session(session_id)
        except Exception as e:
            logger.warning(f"召回长期记忆失败: {e}")
            return ""

    async def create_runner(self, session: Session) -> AgentTaskRunner:
        session_scope = self._scope_for_session(session)
        async with self._uow_factory() as uow:
            patrol_repository = getattr(uow, "patrol", None)
            patrol_run = (
                await patrol_repository.get_run_by_session_id(session.id)
                if patrol_repository is not None
                else None
            )
            patrol_remediation = (
                await patrol_repository.get_remediation_by_session_id(session.id)
                if patrol_repository is not None
                else None
            )
        (
            authorized_codebase,
            authorized_codebase_version_id,
            authorized_knowledge_base,
            authorized_knowledge_base_version_id,
        ) = await self._authorize_session_resources(session, session_scope)
        await self._refresh_runtime_config(session)

        latest_message = await self._latest_user_message(session.id)
        llm, agent_config, skill, skill_prompt, ltm_block, llm_model = await self._resolve_llm_and_config(
            session,
            latest_message,
        )
        is_patrol = bool(skill and skill.slug == "ops-patrol")
        patrol_server_name, agent_config = await resolve_patrol_binding(
            self._uow_factory,
            self._mcp_connection_pool,
            session,
            session_scope,
            is_patrol,
            patrol_run,
            agent_config,
            self._patrol_run_service is not None,
        )

        is_remediation, remediation_prompt_block = await resolve_remediation_binding(
            self._uow_factory,
            self._mcp_connection_pool,
            session,
            session_scope,
            skill,
            patrol_remediation,
            self._patrol_remediation_service is not None,
        )
        # Single boolean for the many "this session is governed to exactly
        # one bound tool" gates below (Ask/subagent/memory/artifact/image
        # tools all get suppressed identically for both branches) — see
        # task-3-report.md for the per-site convergence judgment.
        is_governed_single_tool_session = is_patrol or is_remediation
        if remediation_prompt_block:
            skill_prompt = f"{skill_prompt}\n\n{remediation_prompt_block}".strip() if skill_prompt else remediation_prompt_block
        model_id = llm_model.id
        prompt_locale = detect_locale_from_text(latest_message)

        on_ready = None
        if (
            authorized_codebase
            and session.mode == SessionMode.AGENT
            and self._codebase_service
            and self._object_storage
        ):
            codebase_id = authorized_codebase.id
            codebase_service = self._codebase_service
            object_storage = self._object_storage
            codebase_version_id = authorized_codebase_version_id

            async def ensure_codebase_attached(sandbox: Sandbox) -> None:
                await codebase_service.attach_to_session_sandbox(
                    codebase_id,
                    sandbox,
                    object_storage,
                    scope=session_scope,
                    codebase_version_id=codebase_version_id,
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

        extra_tools = [] if is_governed_single_tool_session else [MemoryTool(save_fn=save_memory_fn, session_id=session.id)]
        if is_patrol and patrol_run and self._patrol_run_service:
            patrol_service = self._patrol_run_service

            async def finalize_patrol(**kwargs):
                finalized = await patrol_service.finalize_run(**kwargs)
                return finalized.model_dump(mode="json")

            extra_tools.append(PatrolTool(finalize_patrol, session_id=session.id))
        if is_remediation and self._patrol_remediation_service:
            remediation_service = self._patrol_remediation_service

            async def execute_remediation(**kwargs):
                return await remediation_service.execute(scope=session_scope, **kwargs)

            extra_tools.append(PatrolRemediationTool(execute_remediation, session_id=session.id))

        runtime = get_runtime_config()
        if not is_governed_single_tool_session and runtime.feature_flags.enable_artifacts and self._artifact_service:
            artifact_service = self._artifact_service

            async def write_artifact(**kwargs):
                content = kwargs.get("content") or ""
                source_path = kwargs.get("source_path")
                if source_path:
                    read_result = await sandbox.read_file(source_path, max_length=None)
                    if not read_result.success:
                        raise ValueError(
                            f"无法从沙箱读取交付物源文件[{source_path}]: {read_result.message or '读取失败'}"
                        )
                    file_content = (read_result.data or {}).get("content")
                    if not isinstance(file_content, str) or not file_content:
                        raise ValueError(f"沙箱文件[{source_path}]内容为空")
                    content = file_content
                elif not content:
                    raise ValueError("artifact_write 需要 content 或 source_path 至少其一")
                artifact, event = await artifact_service.write_content(
                    session_id=session.id,
                    artifact_id=kwargs.get("artifact_id"),
                    kind=kwargs["kind"],
                    title=kwargs["title"],
                    content=content,
                )
                return artifact.model_dump(mode="json"), event

            async def finalize_artifact(artifact_id: str):
                artifact, event = await artifact_service.finalize(session.id, artifact_id)
                return artifact.model_dump(mode="json"), event

            extra_tools.append(ArtifactTool(write_fn=write_artifact, finalize_fn=finalize_artifact))
            skill_prompt = (
                f"{skill_prompt}\n\n交付物规则：产出最终文档或网页时必须调用 artifact_write / artifact_finalize，"
                f"不要仅用 write_file 写入交付物。长文档先用 write_file 写入沙箱，"
                f"再调用 artifact_write 时传 source_path（沙箱路径），不要内联大段 content。"
            ).strip() if skill_prompt else (
                "交付物规则：产出最终文档或网页时必须调用 artifact_write / artifact_finalize。"
                "长文档先用 write_file 写入沙箱，再传 source_path，不要内联大段 content。"
            )

        codebase_prompt = ""
        if authorized_codebase:
            codebase = authorized_codebase
            if session.mode == SessionMode.AGENT:
                codebase_prompt = CODE_AGENT_SKILL_PROMPT
            elif session.mode == SessionMode.ASK:
                codebase_prompt = CODE_ASK_SKILL_PROMPT.format(
                    name=codebase.name,
                    workspace_path=codebase.workspace_path,
                )
            source_reader = None
            if (
                session.mode == SessionMode.ASK
                and authorized_codebase_version_id
            ):
                source_reader = await self._build_versioned_code_source(
                    codebase.id,
                    authorized_codebase_version_id,
                )
            extra_tools.append(
                CodebaseTool(
                    uow_factory=self._uow_factory,
                    codebase_id=codebase.id,
                    sandbox=sandbox,
                    workspace_path=codebase.workspace_path,
                    version_id=authorized_codebase_version_id,
                    source_reader=source_reader,
                    base_version_id=authorized_codebase_version_id,
                )
            )
        if codebase_prompt:
            skill_prompt = f"{skill_prompt}\n\n{codebase_prompt}".strip() if skill_prompt else codebase_prompt
        knowledge_base_prompt = ""
        if authorized_knowledge_base:
            kb = authorized_knowledge_base
            if session.mode == SessionMode.AGENT:
                knowledge_base_prompt = DOC_AGENT_SKILL_PROMPT
            extra_tools.append(
                KnowledgeBaseTool(
                    uow_factory=self._uow_factory,
                    kb_id=kb.id,
                    version_id=authorized_knowledge_base_version_id,
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
        if not is_governed_single_tool_session and caps and caps.image_generation:
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
        tool_gate_override = None
        gate_profile = session.gate_profile
        operator_domains = list(session.operator_domains or [])
        if session.operator_scope:
            profile_key = (gate_profile or "standard").lower()
            profile_settings = runtime.hitl.gate_profiles.get(profile_key)
            if profile_settings is None:
                profile_settings = runtime.hitl.gate_profiles.get("standard")
            if profile_settings is not None:
                tool_gate_override = profile_settings.tool_gate_call_level_enabled
        elif skill and skill.agent_params:
            tool_gate_override = skill.agent_params.tool_gate_call_level_enabled

        agent_runtime_settings = AgentRuntimeSettings(
            tool_timeout_seconds=runtime.worker.tool_timeout_seconds,
            tool_gate_call_level_enabled=tool_gate_override,
            gate_profile=gate_profile if session.operator_scope else None,
            operator_domains=operator_domains,
            memory=runtime_settings,
        )

        import asyncio
        stateful_tool_lock = asyncio.Lock()
        allowed_for_subagent = skill.allowed_tools if (skill and skill.allowed_tools) else None
        session_policy = SessionFlowResolver.resolve(
            session.mode,
            has_kb=authorized_knowledge_base is not None,
            has_codebase=authorized_codebase is not None,
        ).policy
        if allowed_for_subagent is not None:
            session_policy = CapabilityPolicy.for_mode(
                session.mode,
                allowed_tool_names=allowed_for_subagent,
            )
        subagent_overrides = {}
        if skill:
            params = skill.agent_params
            subagent_overrides = {
                "writing_style_override": params.writing_style_override if params else None,
                "override_base_rules": skill.override_base_rules,
            }
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
            prompt_locale=prompt_locale,
            parent_policy=session_policy,
            **subagent_overrides,
        )
        if not is_governed_single_tool_session:
            extra_tools = list(extra_tools) + [subagent_tool]

        # filter_mcp_config_by_refs([]) treats an empty ref list as "no
        # filter" (falsy) and returns *every* enabled server — not what we
        # want for is_remediation, which must expose zero MCP servers to the
        # LLM (the Actuator is only ever called server-side, inside
        # PatrolRemediationService.execute()). Build an explicit empty
        # MCPConfig() instead of routing an empty list through the filter.
        mcp_config = (
            MCPConfig()
            if is_remediation
            else exclude_actuator_server(
                filter_mcp_config_by_refs(
                    self._mcp_config,
                    [patrol_server_name] if is_patrol and patrol_server_name else (skill.mcp_server_refs if skill else None),
                )
            )
        )
        a2a_config = filter_a2a_config_by_refs(
            A2AConfig() if is_governed_single_tool_session else self._a2a_config,
            [] if is_governed_single_tool_session else (skill.a2a_server_refs if skill else None),
        )
        if is_patrol and len(mcp_config.mcpServers) != 1:
            raise NotFoundError("Patrol Session 必须且只能绑定一个 Collector")

        async def on_complete(session_id: str) -> None:
            if self._auto_extract_memory:
                extractor = MemoryExtractorService(
                    uow_factory=self._uow_factory,
                    llm=llm,
                    json_parser=self._json_parser,
                )
                await extractor.extract_from_session(session_id)

        async def on_session_terminal(session_id: str, status) -> None:
            if is_patrol and self._patrol_run_service:
                await self._patrol_run_service.mark_run_failed(session_id)
            if is_remediation and self._patrol_remediation_service:
                await self._patrol_remediation_service.cancel_if_pending(session_id)
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
                        report_md = await self._audit_service.build_session_audit_report(session_id)
                        report_json = await self._audit_service.build_session_audit_report_json_text(session_id)
                        await self._artifact_service.write_content(
                            session_id=session_id,
                            artifact_id=None,
                            kind="doc",
                            title="audit-report.md",
                            content=report_md,
                        )
                        await self._artifact_service.write_content(
                            session_id=session_id,
                            artifact_id=None,
                            kind="doc",
                            title="audit-report.json",
                            content=report_json,
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
            on_complete_callback=(
                on_complete
                if self._auto_extract_memory and not is_governed_single_tool_session
                else None
            ),
            on_session_terminal_callback=on_session_terminal,
            model_id=model_id,
            checkpoint_service=self._checkpoint_service,
            mode=session.mode,
            codebase_id=(
                authorized_codebase.id if authorized_codebase else None
            ),
            knowledge_base_id=(
                authorized_knowledge_base.id
                if authorized_knowledge_base
                else None
            ),
            task_state_port=self._task_state_port,
            observability_port=self._observability_port,
            event_sequence_port=self._event_sequence_port,
            runtime_settings=agent_runtime_settings,
            mcp_connection_pool=self._mcp_connection_pool,
            a2a_connection_pool=self._a2a_connection_pool,
            stateful_tool_lock=stateful_tool_lock,
            owner_user_id=session.owner_user_id,
            team_id=session.team_id,
        )
