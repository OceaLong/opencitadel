#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, AsyncGenerator, Callable, BinaryIO, Optional, Tuple

from fastapi import UploadFile
from pydantic import TypeAdapter

from app.domain.external.browser import Browser
from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.external.task import TaskRunner, Task
from app.domain.models.app_config import AgentConfig, MCPConfig, A2AConfig
from app.domain.models.checkpoint import CheckpointAnchorType
from app.domain.models.event import ErrorEvent, Event, MessageEvent, BaseEvent, ToolEvent, \
    TitleEvent, WaitEvent, DoneEvent, AssistantNoticeEvent, StepEvent, StepEventStatus, ToolEventStatus
from app.domain.services.checkpoint_service import CheckpointService
from app.domain.models.event_policy import should_persist_event
from app.domain.models.file import File
from app.domain.models.message import Message, VisionAttachment
from app.domain.utils.vision import is_image_mime, is_video_mime
from app.domain.services import vision_service
from app.domain.models.session import SessionStatus
from app.domain.models.skill import Skill
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.flows.planner_react import PlannerReActFlow
from app.domain.services.tool_event_presenter import FILE_MUTATING_FUNCTIONS, ToolEventPresenter
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.mcp import MCPTool
from app.application.services.session_state_service import SessionStateService
from app.infrastructure.observability.otel import record_agent_cancel
from app.infrastructure.external.task.task_state import get_task_state

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
            skill: Optional[Skill] = None,
            skill_prompt: str = "",
            long_term_memory_block: str = "",
            extra_tools: Optional[list] = None,
            on_complete_callback: Optional[Callable] = None,
            model_id: Optional[str] = None,
            checkpoint_service: Optional[CheckpointService] = None,
    ) -> None:
        """构造函数，完成Agent任务运行器的创建"""
        self._uow_factory = uow_factory
        self._uow = uow_factory()
        self._session_id = session_id
        self._sandbox = sandbox
        self._mcp_config = mcp_config
        self._mcp_tool = MCPTool()
        self._a2a_config = a2a_config
        self._a2a_tool = A2ATool()
        self._file_storage = file_storage
        self._browser = browser
        self._on_complete_callback = on_complete_callback
        self._session_state = SessionStateService(uow_factory)
        self._event_persist_buffer: List[Tuple[BaseEvent, Dict[str, Any]]] = []
        self._event_persist_batch_size = 5
        self._step_started_at: Dict[str, datetime] = {}
        self._tool_started_at: Dict[str, datetime] = {}
        self._last_observable_event_id: Optional[str] = None
        self._checkpoint_service = checkpoint_service
        self._tool_presenter = ToolEventPresenter(
            sandbox=sandbox,
            browser=browser,
            file_storage=file_storage,
            sync_file_to_storage=self._sync_file_to_storage,
            get_stream_size=self._get_stream_size,
        )
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
        )

    async def _put_and_add_event(self, task: Task, event: Event) -> None:
        """往指定任务的消息队列中添加事件"""
        # 1.往任务的输出消息队列中新增事件
        event_id = await task.output_stream.put(event.model_dump_json())
        event.id = event_id
        if isinstance(event, (StepEvent, ToolEvent)):
            self._last_observable_event_id = event.id

        # 2.仅持久化稳定、高价值事件，流式 delta 不落库
        if should_persist_event(event):
            self._event_persist_buffer.append((event, event.model_dump(mode="json")))
            if len(self._event_persist_buffer) >= self._event_persist_batch_size:
                await self._flush_event_persist_buffer()

    async def _flush_event_persist_buffer(self) -> None:
        """批量刷入已持久化事件，降低数据库往返次数。"""
        if not self._event_persist_buffer:
            return
        payloads = self._event_persist_buffer
        self._event_persist_buffer = []
        async with self._uow:
            await self._uow.session.add_event_payloads(self._session_id, payloads)

    async def _emit_session_status(self, task: Task, status: SessionStatus) -> None:
        """推送服务端权威会话状态事件"""
        event = await self._session_state.transition(self._session_id, status, emit_event=True)
        if event:
            await self._put_and_add_event(task, event)

    async def _is_cancelled(self, task: Task) -> bool:
        return await get_task_state().is_cancelled(task.id)

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

    async def _sync_file_to_sandbox(self, file_id: str) -> File:
        """根据文件id将文件同步到沙箱中"""
        try:
            # 1.调用文件存储下载文件信息
            file_data, file = await self._file_storage.download_file(file_id)

            # 2.组装沙箱文件路径
            filepath = f"/home/ubuntu/upload/{file.filename}"

            # 3.调用沙箱将文件上传至沙箱
            tool_result = await self._sandbox.upload_file(
                file_data=file_data,
                filepath=filepath,
                filename=file.filename
            )

            # 4.判断是否上传成功
            if tool_result.success:
                file.filepath = filepath
                async with self._uow:
                    await self._uow.file.save(file)  # 可以更新也可以不更新
                return file
        except Exception as e:
            logger.exception(f"AgentTaskRunner同步文件[{file_id}]失败: {str(e)}")

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

    async def _sync_message_attachments_to_sandbox(self, event: MessageEvent) -> None:
        """将消息事件中的附件同步到沙箱中"""
        # 1.定义附件列表
        attachments: List[str] = []

        try:
            # 2.判断消息中是否存在附件
            if event.attachments:
                # 3.循环遍历所有的消息附件
                for attachment in event.attachments:
                    # 4.根据同步文件的id将数据同步到沙箱中
                    file = await self._sync_file_to_sandbox(attachment.id)

                    # 5.文件是否同步成功
                    if file:
                        attachments.append(file)
                        async with self._uow:
                            await self._uow.session.add_file(self._session_id, file)

            # 6.更新消息事件中的attachments
            event.attachments = attachments
        except Exception as e:
            logger.exception(f"AgentTaskRunner同步消息附件到沙箱失败: {str(e)}")

    @classmethod
    def _get_stream_size(cls, f: BinaryIO) -> int:
        """根据传递的文件流，获取计算文件的大小"""
        # 1.记录当前文件指针位置
        current_pos = f.tell()

        # 2.将指针移动到文件末尾, seek，0: 偏移量、2: 相对文件末尾
        f.seek(0, 2)

        # 3.获取当前位置，也就是文件大小
        size = f.tell()

        # 4.恢复指针到原始位置
        f.seek(current_pos)

        return size

    async def _sync_file_to_storage(self, filepath: str) -> Optional[File]:
        """将沙箱中指定的文件路径数据同步到存储桶中"""
        try:
            exists_result = await self._sandbox.check_file_exists(filepath)
            exists = (exists_result.data or {}).get("exists", False)
            if not exists_result.success or not exists:
                logger.warning(
                    "会话[%s] 跳过附件同步，沙箱文件不存在: %s",
                    self._session_id,
                    filepath,
                )
                return None

            # 1.根据文件路径从会话中查找文件数据
            async with self._uow:
                file = await self._uow.session.get_file_by_path(self._session_id, filepath)

            # 2.从沙箱中下载文件
            file_data = await self._sandbox.download_file(filepath)

            # 3.判断会话中的文件是否存在
            if file:
                async with self._uow:
                    await self._uow.session.remove_file(self._session_id, file.filepath)

            # 4.提取文件名字、文件信息并更新文件路径
            filename = filepath.split("/")[-1]
            upload_file = UploadFile(
                file=file_data,
                filename=filename,
                size=self._get_stream_size(file_data),
            )

            # 5.上传文件到文件存储桶
            file = await self._file_storage.upload_file(upload_file)
            file.filepath = filepath

            # 6.往会话中新增一个文件信息
            async with self._uow:
                await self._uow.session.add_file(self._session_id, file)
            return file
        except Exception as e:
            logger.warning(
                "会话[%s] 同步文件到存储桶失败 filepath=%s error=%s",
                self._session_id,
                filepath,
                e,
            )
            return None

    async def _sync_message_attachments_to_storage(self, event: MessageEvent) -> None:
        """将消息事件的附件同步到文件存储桶中"""
        # 1.定义附件列表存储数据
        attachments: List[File] = []

        try:
            # 2.判断消息中是否存在附件
            if event.attachments:
                # 3.循环遍历所有附件
                for attachment in event.attachments:
                    # 4.根据文件路径将数据同步到文件存储桶
                    file = await self._sync_file_to_storage(attachment.filepath)
                    if file:
                        attachments.append(file)

            # 5.更新时间中的附件列表资源
            event.attachments = attachments
        except Exception as e:
            logger.exception(f"AgentTaskRunner同步消息附件到存储桶失败: {str(e)}")

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
            event.parent_event_id = event.parent_event_id or self._last_observable_event_id

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
                await self._sync_message_attachments_to_storage(event)

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

    async def invoke(self, task: Task) -> None:
        """根据传递的任务处理agent消息队列并运行agent流"""
        cancelled = False
        try:
            logger.info(f"AgentTaskRunner任务处理开始")
            await self._sandbox.ensure_sandbox()
            await self._mcp_tool.initialize(self._mcp_config)
            await self._a2a_tool.initialize(self._a2a_config)
            await self._emit_session_status(task, SessionStatus.RUNNING)

            while not await task.input_stream.is_empty():
                if await self._is_cancelled(task):
                    cancelled = True
                    break

                event = await self._pop_event(task)
                if event is None:
                    continue
                message = ""

                if isinstance(event, MessageEvent):
                    message = event.message or ""
                    await self._sync_message_attachments_to_sandbox(event)
                    logger.info(f"AgentTaskRunner接收到新消息: {message[:50]}...")

                synced_files = event.attachments or []
                message_obj = Message(
                    message=message,
                    attachments=[attachment.filepath for attachment in synced_files],
                    vision_attachments=await self._build_vision_attachments(synced_files),
                )

                if (
                    self._checkpoint_service
                    and isinstance(event, MessageEvent)
                    and event.role == "user"
                    and event.id
                ):
                    try:
                        await self._checkpoint_service.create_checkpoint(
                            session_id=self._session_id,
                            anchor_type=CheckpointAnchorType.USER_MESSAGE,
                            anchor_event_id=event.id,
                            label=(event.message or "用户消息")[:200],
                            sandbox=self._sandbox,
                        )
                    except Exception as exc:
                        logger.warning("创建用户消息还原点失败: %s", exc)

                async for event in self._run_flow(message_obj):
                    if await self._is_cancelled(task):
                        cancelled = True
                        break

                    await self._put_and_add_event(task, event)

                    if (
                        self._checkpoint_service
                        and isinstance(event, StepEvent)
                        and event.status == StepEventStatus.STARTED
                        and event.id
                    ):
                        try:
                            await self._flush_event_persist_buffer()
                            step_label = event.step.description if event.step else "执行步骤"
                            await self._checkpoint_service.create_checkpoint(
                                session_id=self._session_id,
                                anchor_type=CheckpointAnchorType.STEP,
                                anchor_event_id=event.id,
                                label=step_label[:200],
                                sandbox=self._sandbox,
                            )
                        except Exception as exc:
                            logger.warning("创建步骤还原点失败: %s", exc)

                    if isinstance(event, TitleEvent):
                        async with self._uow:
                            await self._uow.session.update_title(self._session_id, event.title)
                    elif isinstance(event, (MessageEvent, AssistantNoticeEvent)):
                        async with self._uow:
                            await self._uow.session.update_latest_message(
                                self._session_id,
                                event.message,
                                event.created_at,
                            )
                            await self._uow.session.increment_unread_message_count(self._session_id)
                    elif isinstance(event, WaitEvent):
                        await self._emit_session_status(task, SessionStatus.WAITING)
                        await self._flush_event_persist_buffer()
                        return

                    if not await task.input_stream.is_empty():
                        break

                if cancelled:
                    break

            if cancelled:
                await self._put_and_add_event(task, DoneEvent())
                await self._emit_session_status(task, SessionStatus.CANCELLED)
                await self._flush_event_persist_buffer()
                record_agent_cancel(self._session_id)
            else:
                await self._emit_session_status(task, SessionStatus.COMPLETED)
                await self._flush_event_persist_buffer()
        except asyncio.CancelledError:
            logger.info(f"AgentTaskRunner任务运行取消")
            await self._put_and_add_event(task, DoneEvent())
            await self._emit_session_status(task, SessionStatus.CANCELLED)
            await self._flush_event_persist_buffer()
            record_agent_cancel(self._session_id)
            raise
        except Exception as e:
            logger.exception(f"AgentTaskRunner运行出错: {str(e)}")
            await self._put_and_add_event(task, ErrorEvent(error=f"AgentTaskRunner出错: {str(e)}"))
            await self._emit_session_status(task, SessionStatus.COMPLETED)
            await self._flush_event_persist_buffer()
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
        if self._sandbox:
            logger.info("销毁AgentTaskRunner中的沙箱环境")
            await self._sandbox.destroy()

    async def on_done(self, task: Task) -> None:
        """任务结束时执行的回调函数"""
        logger.info(f"AgentTaskRunner任务执行结束")
        if self._on_complete_callback:
            try:
                asyncio.create_task(self._on_complete_callback(self._session_id))
            except Exception as e:
                logger.warning(f"后台完成回调创建失败: {e}")
