#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, AsyncGenerator, Callable, Optional
from pydantic import TypeAdapter

from app.domain.external.browser import Browser
from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.external.task import RecoverableTaskInputUnavailable, TaskRunner, Task
from app.domain.models.app_config import AgentConfig, MCPConfig, A2AConfig
from app.domain.models.event import ErrorEvent, Event, MessageEvent, BaseEvent, ToolEvent, \
    TitleEvent, WaitEvent, DoneEvent, AssistantNoticeEvent, StepEvent, StepEventStatus, ToolEventStatus
from app.domain.services.checkpoint_service import CheckpointService
from app.domain.models.file import File
from app.domain.models.message import Message, VisionAttachment
from app.domain.utils.vision import is_image_mime, is_video_mime
from app.domain.services import vision_service
from app.domain.models.codebase import SessionMode
from app.domain.models.session import SessionStatus
from app.domain.models.skill import Skill
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agent.attachment_sync import AgentAttachmentSyncer
from app.domain.services.agent.event_emitter import AgentEventEmitter
from app.domain.services.agent.sandbox_lifecycle import SandboxLifecycleCoordinator
from app.domain.services.agent.sandbox_provider import SandboxProvider
from app.domain.services.flows.planner_react import PlannerReActFlow
from app.domain.services.flows.code_ask_flow import CodeAskFlow
from app.domain.services.flows.doc_qa_flow import DocQAFlow
from app.domain.services.tool_event_presenter import FILE_MUTATING_FUNCTIONS, ToolEventPresenter
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.mcp import MCPTool
from app.domain.external.connection_pool import A2AConnectionPoolPort, MCPConnectionPoolPort
from app.domain.external.event_sequence import EventSequencePort
from app.domain.models.agent_runtime_settings import AgentRuntimeSettings
from app.domain.external.observability import ObservabilityPort
from app.domain.external.session_state import SessionStatePort
from app.domain.external.task_state_port import TaskStatePort

logger = logging.getLogger(__name__)


class AgentTaskRunner(TaskRunner):
    """基于Agent智能体的任务运行器"""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],  # uow模块
            llm: LLM,  # 大语言模型
            agent_config: AgentConfig,  # 智能体配置
            mcp_config: MCPConfig,  # mcp配置
            a2a_config: A2AConfig,  # a2a配置
            session_id: str,  # 会话id
            file_storage: FileStorage,  # 文件存储桶
            json_parser: JSONParser,  # json解析器
            browser: Browser,  # 浏览器
            search_engine: SearchEngine,  # 搜索引擎
            sandbox: Sandbox,  # 沙箱
            sandbox_provider: SandboxProvider,
            task_state_port: TaskStatePort,
            observability_port: ObservabilityPort,
            event_sequence_port: EventSequencePort,
            session_state_port: SessionStatePort,
            runtime_settings: AgentRuntimeSettings,
            mcp_connection_pool: MCPConnectionPoolPort,
            a2a_connection_pool: A2AConnectionPoolPort,
            skill: Optional[Skill] = None,
            skill_prompt: str = "",
            long_term_memory_block: str = "",
            extra_tools: Optional[list] = None,
            on_complete_callback: Optional[Callable] = None,
            on_session_terminal_callback: Optional[Callable] = None,
            model_id: Optional[str] = None,
            checkpoint_service: Optional[CheckpointService] = None,
            mode: SessionMode = SessionMode.AGENT,
            codebase_id: Optional[str] = None,
            knowledge_base_id: Optional[str] = None,
            stateful_tool_lock: Optional[asyncio.Lock] = None,
            owner_user_id: Optional[str] = None,
            team_id: Optional[str] = None,
    ) -> None:
        """构造函数，完成Agent任务运行器的创建"""
        self._uow_factory = uow_factory
        self._session_id = session_id
        self._sandbox = sandbox
        self._sandbox_provider = sandbox_provider
        self._mcp_config = mcp_config
        self._mcp_tool = MCPTool(mcp_connection_pool)
        self._a2a_config = a2a_config
        self._a2a_tool = A2ATool(a2a_connection_pool)
        self._file_storage = file_storage
        self._browser = browser
        self._on_complete_callback = on_complete_callback
        self._on_session_terminal_callback = on_session_terminal_callback
        self._runtime_settings = runtime_settings
        self._session_state = session_state_port
        self._task_state_port = task_state_port
        self._observability = observability_port
        self._event_emitter = AgentEventEmitter(
            session_id=session_id,
            uow_factory=uow_factory,
            event_sequence=event_sequence_port,
            task_state_port=task_state_port,
        )
        self._attachment_sync = AgentAttachmentSyncer(
            session_id=session_id,
            uow_factory=uow_factory,
            sandbox=sandbox,
            file_storage=file_storage,
            owner_user_id=owner_user_id,
            team_id=team_id,
        )
        self._sandbox_lifecycle = SandboxLifecycleCoordinator(
            session_id=session_id,
            sandbox_provider=sandbox_provider,
            checkpoint_service=checkpoint_service,
        )
        self._agent_config = agent_config
        self._run_started_at: Optional[float] = None
        self._terminal_session_status: Optional[SessionStatus] = None
        self._step_started_at: Dict[str, datetime] = {}
        self._tool_started_at: Dict[str, datetime] = {}
        self._last_step_checkpoint_at: float = 0.0
        self._step_checkpoint_min_interval_seconds = 60.0
        self._checkpoint_service = checkpoint_service
        self._tool_presenter = ToolEventPresenter(
            sandbox=sandbox,
            browser=browser,
            file_storage=file_storage,
            sync_file_to_storage=self._attachment_sync.sync_file_to_storage,
            get_stream_size=AgentAttachmentSyncer.get_stream_size,
            owner_user_id=owner_user_id,
            team_id=team_id,
        )
        self._mode = mode
        self._codebase_id = codebase_id
        self._knowledge_base_id = knowledge_base_id
        self._stateful_tool_lock = stateful_tool_lock or asyncio.Lock()
        if codebase_id and mode == SessionMode.ASK:
            self._flow = CodeAskFlow(
                uow_factory=uow_factory,
                llm=llm,
                agent_config=agent_config,
                session_id=session_id,
                json_parser=json_parser,
                browser=browser,
                sandbox=sandbox,
                search_engine=search_engine,
                mcp_tool=self._mcp_tool,
                a2a_tool=self._a2a_tool,
                extra_tools=extra_tools or [],
                model_id=model_id,
                observability_port=self._observability,
                runtime_settings=self._runtime_settings,
            )
        elif knowledge_base_id and mode == SessionMode.ASK:
            self._flow = DocQAFlow(
                uow_factory=uow_factory,
                llm=llm,
                agent_config=agent_config,
                session_id=session_id,
                json_parser=json_parser,
                browser=browser,
                sandbox=sandbox,
                search_engine=search_engine,
                mcp_tool=self._mcp_tool,
                a2a_tool=self._a2a_tool,
                extra_tools=extra_tools or [],
                model_id=model_id,
                observability_port=self._observability,
                runtime_settings=self._runtime_settings,
            )
        else:
            self._flow = PlannerReActFlow(
                uow_factory=uow_factory,
                llm=llm,
                agent_config=agent_config,
                session_id=session_id,
                json_parser=json_parser,
                browser=browser,
                sandbox=sandbox,
                search_engine=search_engine,
                mcp_tool=self._mcp_tool,
                a2a_tool=self._a2a_tool,
                skill=skill,
                skill_prompt=skill_prompt,
                long_term_memory_block=long_term_memory_block,
                extra_tools=extra_tools or [],
                model_id=model_id,
                file_storage=file_storage,
                observability_port=self._observability,
                runtime_settings=self._runtime_settings,
                stateful_tool_lock=self._stateful_tool_lock,
            )

    async def _put_and_add_event(self, task: Task, event: Event) -> None:
        await self._event_emitter.emit(task, event)

    async def _flush_event_persist_buffer(self) -> None:
        await self._event_emitter.flush()

    async def _emit_session_status(self, task: Task, status: SessionStatus) -> None:
        """推送服务端权威会话状态事件"""
        if status != SessionStatus.RUNNING:
            self._terminal_session_status = status
        event = await self._session_state.transition(self._session_id, status, emit_event=True)
        if event:
            await self._put_and_add_event(task, event)
        if status != SessionStatus.RUNNING and self._on_session_terminal_callback:
            try:
                await self._on_session_terminal_callback(self._session_id, status)
            except Exception as exc:
                logger.warning("会话终态回调失败 session=%s: %s", self._session_id, exc)

    async def _is_cancelled(self, task: Task) -> bool:
        return await self._task_state_port.is_cancelled(task.id)

    def _is_run_timed_out(self) -> bool:
        if self._run_started_at is None:
            return False
        max_seconds = getattr(self._agent_config, "max_run_seconds", 3600) or 3600
        return (time.monotonic() - self._run_started_at) >= max_seconds

    async def _handle_run_timeout(self, task: Task) -> None:
        await self._put_and_add_event(task, ErrorEvent(error="Agent 运行超时，任务已终止"))
        await self._emit_session_status(task, SessionStatus.FAILED)
        await self._flush_event_persist_buffer()
        raise RuntimeError("Agent 运行超时")

    @classmethod
    async def _pop_event(cls, task: Task) -> Optional[Event]:
        """从任务的输入流中获取事件信息"""
        # 1.从任务task中读取数据
        event_id, event_str = await task.input_stream.pop()
        if event_str is None:
            logger.warning(f"AgentTaskRunner接收到空消息")
            return

        # 2.使用pydantic+type类型将字符串转换成事件
        event = TypeAdapter(Event).validate_json(event_str)
        event.id = event_id

        return event

    async def _build_vision_attachments(self, files: List[File]) -> List[VisionAttachment]:
        """为多模态模型构建用户附件（图片/音频/视频帧）。"""
        llm = self._flow.react._llm
        attachments = await vision_service.prepare_media_attachments_from_files(
            files, llm, self._file_storage,
        )
        capabilities = vision_service.resolve_capabilities(llm)
        if not capabilities.video:
            return attachments

        for file in files:
            if not is_video_mime(file.mime_type):
                continue
            try:
                file_data, _ = await self._file_storage.download_file(file.id)
                from app.domain.services.video_service import extract_video_frames
                frames = await extract_video_frames(
                    file_data.read(),
                    max_frames=capabilities.max_video_frames,
                    mime_type=file.mime_type,
                )
                attachments.extend(frames)
            except Exception as exc:
                logger.warning("视频抽帧失败 file_id=%s: %s", file.id, exc)
        return attachments

    async def _handle_tool_event(self, event: ToolEvent) -> None:
        """额外处理工具消息，使其前端交互更友好"""
        await self._tool_presenter.enrich(event)

    def _annotate_observable_event(self, event: BaseEvent) -> None:
        """Attach lightweight timing and parent hints for single-task observability."""
        now = event.created_at or datetime.now(timezone.utc)
        if isinstance(event, StepEvent):
            step_id = event.step.id
            if event.step.status.value == "running":
                started_at = self._step_started_at.setdefault(step_id, now)
                event.started_at = event.started_at or started_at
            elif event.step.status.value in {"completed", "failed"}:
                started_at = self._step_started_at.get(step_id)
                event.started_at = event.started_at or started_at
                event.ended_at = event.ended_at or now
                if started_at and event.duration_ms is None:
                    event.duration_ms = max(0, int((event.ended_at - started_at).total_seconds() * 1000))
                event.error = event.error or event.step.error
            event.span_id = event.span_id or f"step:{step_id}"
            return

        if isinstance(event, ToolEvent):
            tool_id = event.tool_call_id
            if event.status == ToolEventStatus.CALLING:
                started_at = self._tool_started_at.setdefault(tool_id, now)
                event.started_at = event.started_at or started_at
            elif event.status == ToolEventStatus.CALLED:
                started_at = self._tool_started_at.get(tool_id)
                event.started_at = event.started_at or started_at
                event.ended_at = event.ended_at or now
                if started_at and event.duration_ms is None:
                    event.duration_ms = max(0, int((event.ended_at - started_at).total_seconds() * 1000))
                if event.function_result and not event.function_result.success:
                    event.error = event.error or event.function_result.message
            event.span_id = event.span_id or f"tool:{tool_id}"
            return

        if isinstance(event, ErrorEvent):
            event.parent_event_id = (
                event.parent_event_id or self._event_emitter.last_observable_event_id
            )

    async def _emit_code_diff_if_needed(self, task: Task) -> None:
        """After agent code changes, emit git diff summary."""
        if self._sandbox_provider.materialized() is None:
            return
        try:
            async with self._uow_factory() as uow:
                codebase = await uow.codebase.get_by_id(self._codebase_id)
            if not codebase or not codebase.workspace_path:
                return
            workspace = codebase.workspace_path
            result = await self._sandbox.exec_command(
                "diff",
                workspace,
                f"cd {workspace} && git diff 2>/dev/null || diff -ruN /dev/null . 2>/dev/null | head -500",
            )
            if result.success and result.data and result.data.strip():
                diff_text = result.data[:12000]
                await self._put_and_add_event(
                    task,
                    MessageEvent(
                        role="assistant",
                        message=f"## 代码变更 Diff\n\n```diff\n{diff_text}\n```\n\n"
                                f"可使用代码库下载接口获取完整项目包。",
                    ),
                )
        except Exception as exc:
            logger.warning("生成代码 diff 失败: %s", exc)

    async def _run_flow(self, message: Message) -> AsyncGenerator[BaseEvent, None]:
        """根据消息对象运行PlannerReActFlow"""
        # 1.判断传递的消息是否为空
        if not message.message:
            logger.warning(f"AgentTaskRunner接收了一条空消息")
            yield ErrorEvent(error="空消息错误")
            return

        # 2.调用流并运行获取事件信息
        async for event in self._flow.invoke(message):
            # 3.判断是否为工具事件，如果是则额外处理
            if isinstance(event, ToolEvent):
                await self._handle_tool_event(event)
            elif isinstance(event, MessageEvent):
                # 4.如果是消息事件则将AI消息事件中的附件同步到存储中
                await self._attachment_sync.sync_message_attachments_to_storage(event)

            self._annotate_observable_event(event)

            # 5.将事件直接返回
            yield event

    async def _cleanup_tools(self) -> None:
        """清理MCP和A2A工具资源，确保在同一任务上下文中释放

        注意：该方法必须在初始化MCP/A2A的同一个asyncio Task中调用，
        否则anyio的cancel scope会检测到任务上下文切换并抛出RuntimeError。
        """
        try:
            if self._mcp_tool:
                await self._mcp_tool.cleanup()
        except Exception as e:
            logger.warning(f"清理MCP工具资源时出错: {e}")
        try:
            if self._a2a_tool:
                await self._a2a_tool.cleanup()
        except Exception as e:
            logger.warning(f"清理A2A工具资源时出错: {e}")
        try:
            if self._browser:
                await self._browser.cleanup()
        except Exception as e:
            logger.warning(f"清理Browser资源时出错: {e}")

    async def invoke(self, task: Task) -> None:
        """根据传递的任务处理agent消息队列并运行agent流"""
        cancelled = False
        try:
            logger.info(f"AgentTaskRunner任务处理开始")
            await self._sandbox_lifecycle.ensure_ready()
            await self._mcp_tool.initialize(self._mcp_config)
            await self._a2a_tool.initialize(self._a2a_config)
            await self._emit_session_status(task, SessionStatus.RUNNING)
            self._run_started_at = time.monotonic()

            processed_input = False
            while True:
                if self._is_run_timed_out():
                    await self._handle_run_timeout(task)
                    return
                if await self._is_cancelled(task):
                    cancelled = True
                    break

                event = await self._pop_event(task)
                if event is None:
                    if not processed_input:
                        logger.warning(
                            "任务[%s]在运行态未读取到输入，等待重启恢复对账",
                            task.id,
                        )
                        raise RecoverableTaskInputUnavailable(
                            f"任务[{task.id}]缺少可执行输入，等待恢复对账",
                        )
                    break
                processed_input = True
                message = ""

                if isinstance(event, MessageEvent):
                    message = event.message or ""
                    if self._sandbox_provider.materialized() is not None:
                        await self._attachment_sync.sync_message_attachments_to_sandbox(event)
                    logger.info(f"AgentTaskRunner接收到新消息: {message[:50]}...")

                synced_files = event.attachments or []
                message_obj = Message(
                    message=message,
                    attachments=[attachment.filepath for attachment in synced_files],
                    vision_attachments=await self._build_vision_attachments(synced_files),
                )

                if isinstance(event, MessageEvent):
                    await self._sandbox_lifecycle.create_user_message_checkpoint(event)

                async for event in self._run_flow(message_obj):
                    if self._is_run_timed_out():
                        await self._handle_run_timeout(task)
                        return
                    if await self._is_cancelled(task):
                        cancelled = True
                        break

                    await self._put_and_add_event(task, event)

                    if (
                        self._checkpoint_service
                        and isinstance(event, StepEvent)
                        and event.status == StepEventStatus.STARTED
                        and event.id
                        and time.monotonic() - self._last_step_checkpoint_at
                        >= self._step_checkpoint_min_interval_seconds
                    ):
                        self._last_step_checkpoint_at = time.monotonic()
                        try:
                            await self._flush_event_persist_buffer()
                            await self._sandbox_lifecycle.create_step_checkpoint(event)
                        except Exception as exc:
                            logger.warning("创建步骤还原点失败: %s", exc)

                    if isinstance(event, TitleEvent):
                        async with self._uow_factory() as uow:
                            await uow.session.update_title(self._session_id, event.title)
                    elif isinstance(event, (MessageEvent, AssistantNoticeEvent)):
                        async with self._uow_factory() as uow:
                            await uow.session.update_latest_message(
                                self._session_id,
                                event.message,
                                event.created_at,
                            )
                            await uow.session.increment_unread_message_count(self._session_id)
                    elif isinstance(event, WaitEvent):
                        await self._emit_session_status(task, SessionStatus.WAITING)
                        await self._flush_event_persist_buffer()
                        return

                    if await task.input_stream.size() > 0:
                        await self._flush_event_persist_buffer()
                        break

                if cancelled:
                    break

                if self._codebase_id and self._mode == SessionMode.AGENT:
                    if self._sandbox_provider.materialized() is not None:
                        await self._emit_code_diff_if_needed(task)

            if not cancelled and await self._is_cancelled(task):
                cancelled = True

            if cancelled:
                await self._put_and_add_event(task, DoneEvent())
                await self._emit_session_status(task, SessionStatus.CANCELLED)
                await self._flush_event_persist_buffer()
                self._observability.record_agent_cancel(self._session_id)
            else:
                await self._emit_session_status(task, SessionStatus.COMPLETED)
                await self._flush_event_persist_buffer()
        except asyncio.CancelledError:
            logger.info(f"AgentTaskRunner任务运行取消")
            await self._put_and_add_event(task, DoneEvent())
            await self._emit_session_status(task, SessionStatus.CANCELLED)
            await self._flush_event_persist_buffer()
            self._observability.record_agent_cancel(self._session_id)
            raise
        except RecoverableTaskInputUnavailable:
            await self._flush_event_persist_buffer()
            raise
        except Exception as e:
            from app.domain.utils.llm_retry import classify_llm_error_code
            from app.infrastructure.external.llm.resilient_llm import ModelUnavailableError

            logger.exception(f"AgentTaskRunner运行出错: {str(e)}")
            code = e.error_code if isinstance(e, ModelUnavailableError) else classify_llm_error_code(e)
            await self._put_and_add_event(task, ErrorEvent(error=f"AgentTaskRunner出错: {str(e)}", code=code))
            await self._emit_session_status(task, SessionStatus.FAILED)
            await self._flush_event_persist_buffer()
            raise
        finally:
            await self._flush_event_persist_buffer()
            await self._cleanup_tools()

    async def cleanup(self) -> None:
        """清理任务级资源（保留 sandbox 供后续对话复用）"""
        await self._cleanup_tools()

    async def destroy(self) -> None:
        """销毁任务运行器并释放 sandbox 资源"""
        logger.info(f"开始清除销毁AgentTaskRunner资源")
        await self.cleanup()
        if self._sandbox_provider.materialized() is not None:
            logger.info("销毁AgentTaskRunner中的沙箱环境")
            await self._sandbox.destroy()

    async def on_done(self, task: Task) -> None:
        """任务结束时执行的回调函数"""
        logger.info(f"AgentTaskRunner任务执行结束")
        if (
            self._on_complete_callback
            and self._terminal_session_status == SessionStatus.COMPLETED
        ):
            try:
                await self._on_complete_callback(self._session_id)
            except Exception as e:
                logger.warning(f"任务完成回调执行失败: {e}", exc_info=True)
