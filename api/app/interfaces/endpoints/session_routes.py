#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, AsyncGenerator

import websockets
from fastapi import APIRouter, Depends, Body, Query
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets import ConnectionClosed

from app.application.errors.exceptions import NotFoundError
from app.application.services.agent_service import AgentService
from app.application.services.session_service import SessionService
from app.interfaces.schemas import Response
from app.interfaces.schemas.event import EventMapper
from app.interfaces.schemas.session import (
    CreateSessionRequest,
    CreateSessionResponse,
    ListSessionResponse,
    ListSessionItem,
    ChatRequest,
    GetSessionResponse,
    GetSessionFilesResponse,
    FileReadResponse,
    FileReadRequest,
    ShellReadResponse,
    ShellReadRequest,
    UpdateSessionConfigRequest,
    GetSessionTokenUsageResponse,
    TokenUsageSummaryResponse,
    TokenUsageRecordResponse,
    GetSessionEventsResponse,
)
from app.interfaces.schemas.llm_model import LLMModelResponse
from app.interfaces.schemas.skill import SkillSummaryResponse
from app.interfaces.schemas.memory import SessionMemoryResponse, ClearMemoryRequest
from app.interfaces.schemas.checkpoint import (
    CheckpointItemResponse,
    ListCheckpointsResponse,
    RestoreCheckpointResponse,
)
from app.interfaces.endpoints.llm_model_routes import _to_response as llm_to_response
from app.interfaces.service_dependencies import (
    get_session_service,
    get_agent_service,
    get_llm_model_service,
    get_skill_service,
    get_memory_service,
    get_llm_token_usage_service,
)
from app.application.services.llm_token_usage_service import LLMTokenUsageService
from app.application.services.llm_model_service import LLMModelService
from app.application.services.skill_service import SkillService
from app.application.services.memory_service import MemoryService
from app.domain.models.session import Session
from app.domain.models.event import BaseEvent
from app.domain.models.event_policy import should_project_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["会话模块"])


async def build_get_session_response(
        session: Session,
        llm_model_service: LLMModelService,
        skill_service: SkillService,
        token_usage_service: Optional[LLMTokenUsageService] = None,
        include_debug: bool = False,
        event_records: Optional[list[tuple[int, BaseEvent]]] = None,
        event_limit: int = 100,
) -> GetSessionResponse:
    """组装会话详情响应，避免在路由间直接调用 endpoint 函数"""
    model_resp = None
    skill_resp = None
    if session.model_id:
        try:
            model_resp = llm_to_response(await llm_model_service.get_model(session.model_id))
        except Exception:
            pass
    if session.skill_id:
        summary = await skill_service.get_summary(session.skill_id)
        if summary:
            skill_resp = SkillSummaryResponse(**summary.model_dump())
    token_usage_resp = None
    if token_usage_service:
        try:
            model_prices = {}
            if session.model_id:
                try:
                    model = await llm_model_service.get_model(session.model_id, mask=False)
                    model_prices[model.id] = (
                        model.input_price_per_million,
                        model.output_price_per_million,
                    )
                    model_prices[model.model_name] = (
                        model.input_price_per_million,
                        model.output_price_per_million,
                    )
                except Exception:
                    pass
            summary = await token_usage_service.get_session_summary(
                session.id,
                model_prices=model_prices or None,
            )
            token_usage_resp = TokenUsageSummaryResponse(
                prompt_tokens=summary.prompt_tokens,
                completion_tokens=summary.completion_tokens,
                total_tokens=summary.total_tokens,
                estimated_cost_usd=summary.estimated_cost_usd,
                call_count=summary.call_count,
            )
        except Exception as exc:
            logger.debug("获取会话 token 汇总失败: %s", exc)

    if event_records is None:
        events = session.events
        events_next_cursor = None
    else:
        events = [event for _, event in event_records]
        events_next_cursor = event_records[-1][0] if len(event_records) == event_limit else None

    return GetSessionResponse(
        session_id=session.id,
        title=session.title,
        status=session.status,
        events=EventMapper.events_to_sse_events(events, include_debug=include_debug),
        events_next_cursor=events_next_cursor,
        model_id=session.model_id,
        skill_id=session.skill_id,
        thinking_enabled=session.thinking_enabled,
        model=model_resp,
        skill=skill_resp,
        token_usage=token_usage_resp,
    )

# 流式获取会话详情睡眠间隔
SESSION_SLEEP_INTERVAL = 5


@router.post(
    path="",
    response_model=Response[CreateSessionResponse],
    summary="创建新任务会话",
    description="创建一个空白的新任务会话",
)
async def create_session(
        request: CreateSessionRequest = Body(default_factory=CreateSessionRequest),
        session_service: SessionService = Depends(get_session_service),
) -> Response[CreateSessionResponse]:
    """创建一个空白的新任务会话"""
    session = await session_service.create_session(
        title=request.title or "新对话",
        model_id=request.model_id,
        skill_id=request.skill_id,
        thinking_enabled=bool(request.thinking_enabled) if request.thinking_enabled is not None else False,
        codebase_id=request.codebase_id,
        mode=request.mode,
    )
    return Response.success(
        msg="创建任务会话成功",
        data=CreateSessionResponse(session_id=session.id)
    )


@router.post(
    path="/stream",
    summary="流式获取所有会话基础信息列表",
    description="间隔指定时间流式获取所有会话基础信息列表",
)
async def stream_sessions(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        session_service: SessionService = Depends(get_session_service),
) -> EventSourceResponse:
    """间隔指定时间流式获取所有会话基础信息列表"""

    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        """定义一个异步迭代器，用于获取所有会话列表"""
        while True:
            # 1.获取所有会话列表
            sessions = await session_service.get_all_sessions(limit=limit, offset=offset)

            # 2.循环遍历并组装数据
            session_items = [
                ListSessionItem(
                    session_id=session.id,
                    title=session.title,
                    latest_message=session.latest_message,
                    latest_message_at=session.latest_message_at,
                    status=session.status,
                    unread_message_count=session.unread_message_count,
                )
                for session in sessions
            ]

            # 3.将会话列表转换为流式事件数据并返回
            yield ServerSentEvent(
                event="sessions",
                data=ListSessionResponse(sessions=session_items).model_dump_json(),
            )

            # 4.睡眠指定时间避免高频响应
            await asyncio.sleep(SESSION_SLEEP_INTERVAL)

    return EventSourceResponse(event_generator())


@router.get(
    path="",
    response_model=Response[ListSessionResponse],
    summary="获取会话列表基础信息",
    description="获取MyManus项目中所有任务会话基础信息列表",
)
async def get_all_sessions(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        session_service: SessionService = Depends(get_session_service),
) -> Response[ListSessionResponse]:
    """获取MyManus项目中所有任务会话基础信息列表"""
    sessions = await session_service.get_all_sessions(limit=limit, offset=offset)
    session_items = [
        ListSessionItem(
            session_id=session.id,
            title=session.title,
            latest_message=session.latest_message,
            latest_message_at=session.latest_message_at,
            status=session.status,
            unread_message_count=session.unread_message_count,
        )
        for session in sessions
    ]
    return Response.success(
        msg="获取任务会话列表成功",
        data=ListSessionResponse(sessions=session_items)
    )


@router.post(
    path="/{session_id}/clear-unread-message-count",
    response_model=Response[Optional[Dict]],
    summary="清除指定任务会话未读消息数",
    description="清除指定任务会话未读消息数",
)
async def clear_unread_message_count(
        session_id: str,
        session_service: SessionService = Depends(get_session_service),
) -> Response[Optional[Dict]]:
    """根据传递的会话id清空未读消息数"""
    await session_service.clear_unread_message_count(session_id)
    return Response.success(msg="清除未读消息数成功")


@router.post(
    path="/{session_id}/delete",
    response_model=Response[Optional[Dict]],
    summary="删除指定任务会话",
    description="根据传递的会话id删除指定任务会话",
)
async def delete_session(
        session_id: str,
        session_service: SessionService = Depends(get_session_service),
) -> Response[Optional[Dict]]:
    """根据传递的会话id删除指定任务会话"""
    await session_service.delete_session(session_id)
    return Response.success(msg="删除任务会话成功")


@router.post(
    path="/{session_id}/chat",
    summary="向指定任务会话发起聊天请求",
    description="向指定任务会话发起聊天请求"
)
async def chat(
        session_id: str,
        request: ChatRequest,
        include_debug: bool = Query(default=False),
        agent_service: AgentService = Depends(get_agent_service),
        session_service: SessionService = Depends(get_session_service),
) -> EventSourceResponse:
    """根据传递的会话id+chat请求数据向指定会话发起聊天请求"""
    session = await session_service.get_session(session_id)
    if not session:
        raise NotFoundError("该会话不存在，请核实后重试")

    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        """定义事件生成器，用于配合EventSourceResponse生成流式响应数据"""
        # 1.调用Agent服务发起聊天
        async for event in agent_service.chat(
                session_id=session_id,
                message=request.message,
                attachments=request.attachments,
                latest_event_id=request.event_id,
                timestamp=datetime.fromtimestamp(request.timestamp) if request.timestamp else None,
                model_id=request.model_id,
                skill_id=request.skill_id,
                thinking_enabled=request.thinking_enabled,
                mode=request.mode,
        ):
            if not should_project_event(event, include_transient=True, include_debug=include_debug):
                continue
            # 2.将Agent事件转换为sse数据(因为普通的event没法通过流式事件传输)
            sse_event = EventMapper.event_to_sse_event(event)
            if sse_event:
                yield ServerSentEvent(
                    event=sse_event.event,
                    data=sse_event.data.model_dump_json(),
                )

    return EventSourceResponse(event_generator())


@router.get(
    path="/{session_id}/events",
    response_model=Response[GetSessionEventsResponse],
    summary="分页获取指定会话事件",
    description="根据游标分页获取指定会话的持久化事件",
)
async def get_session_events(
        session_id: str,
        after: Optional[int] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        include_debug: bool = Query(default=False),
        session_service: SessionService = Depends(get_session_service),
) -> Response[GetSessionEventsResponse]:
    records = await session_service.get_session_events(session_id, after=after, limit=limit)
    projected = [
        event
        for _, event in records
        if should_project_event(event, include_transient=False, include_debug=include_debug)
    ]
    return Response.success(
        msg="分页获取会话事件成功",
        data=GetSessionEventsResponse(
            events=EventMapper.events_to_sse_events(projected, include_debug=include_debug),
            next_cursor=records[-1][0] if len(records) == limit else None,
        ),
    )


@router.get(
    path="/{session_id}",
    response_model=Response[GetSessionResponse],
    summary="获取指定会话详情信息",
    description="根据传递的会话id获取该会话的对话详情",
)
async def get_session(
        session_id: str,
        include_debug: bool = Query(default=False),
        events_limit: int = Query(default=100, ge=1, le=500),
        session_service: SessionService = Depends(get_session_service),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
        skill_service: SkillService = Depends(get_skill_service),
        token_usage_service: LLMTokenUsageService = Depends(get_llm_token_usage_service),
) -> Response[GetSessionResponse]:
    """传递指定会话id获取该会话的对话详情"""
    session = await session_service.get_session(session_id)
    if not session:
        raise NotFoundError("该会话不存在，请核实后重试")
    event_records = await session_service.get_session_events(session_id, limit=events_limit)
    return Response.success(
        msg="获取会话详情成功",
        data=await build_get_session_response(
            session,
            llm_model_service,
            skill_service,
            token_usage_service,
            include_debug=include_debug,
            event_records=event_records,
            event_limit=events_limit,
        ),
    )


@router.get(
    path="/{session_id}/token-usage",
    response_model=Response[GetSessionTokenUsageResponse],
    summary="获取会话 Token 用量明细",
)
async def get_session_token_usage(
        session_id: str,
        session_service: SessionService = Depends(get_session_service),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
        token_usage_service: LLMTokenUsageService = Depends(get_llm_token_usage_service),
) -> Response[GetSessionTokenUsageResponse]:
    session = await session_service.get_session(session_id)
    if not session:
        raise NotFoundError("该会话不存在，请核实后重试")
    model_prices = {}
    if session.model_id:
        try:
            model = await llm_model_service.get_model(session.model_id, mask=False)
            model_prices[model.id] = (model.input_price_per_million, model.output_price_per_million)
            model_prices[model.model_name] = (model.input_price_per_million, model.output_price_per_million)
        except Exception:
            pass
    summary = await token_usage_service.get_session_summary(session_id, model_prices=model_prices or None)
    records = await token_usage_service.list_by_session(session_id)
    return Response.success(
        msg="获取 Token 用量成功",
        data=GetSessionTokenUsageResponse(
            summary=TokenUsageSummaryResponse(
                prompt_tokens=summary.prompt_tokens,
                completion_tokens=summary.completion_tokens,
                total_tokens=summary.total_tokens,
                estimated_cost_usd=summary.estimated_cost_usd,
                call_count=summary.call_count,
            ),
            records=[
                TokenUsageRecordResponse(
                    id=r.id,
                    agent=r.agent,
                    step=r.step,
                    model_id=r.model_id,
                    model_name=r.model_name,
                    call_type=r.call_type,
                    prompt_tokens=r.prompt_tokens,
                    completion_tokens=r.completion_tokens,
                    total_tokens=r.total_tokens,
                    created_at=r.created_at,
                )
                for r in records
            ],
        ),
    )


@router.patch(
    path="/{session_id}",
    response_model=Response[GetSessionResponse],
    summary="更新会话配置",
)
async def patch_session(
        session_id: str,
        request: UpdateSessionConfigRequest,
        include_debug: bool = Query(default=False),
        events_limit: int = Query(default=100, ge=1, le=500),
        session_service: SessionService = Depends(get_session_service),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
        skill_service: SkillService = Depends(get_skill_service),
        token_usage_service: LLMTokenUsageService = Depends(get_llm_token_usage_service),
) -> Response[GetSessionResponse]:
    await session_service.update_session_config(
        session_id,
        model_id=request.model_id,
        skill_id=request.skill_id,
        thinking_enabled=request.thinking_enabled,
    )
    session = await session_service.get_session(session_id)
    if not session:
        raise NotFoundError("该会话不存在，请核实后重试")
    event_records = await session_service.get_session_events(session_id, limit=events_limit)
    return Response.success(
        msg="更新会话配置成功",
        data=await build_get_session_response(
            session,
            llm_model_service,
            skill_service,
            token_usage_service,
            include_debug=include_debug,
            event_records=event_records,
            event_limit=events_limit,
        ),
    )


@router.get(
    path="/{session_id}/memory",
    response_model=Response[SessionMemoryResponse],
    summary="获取会话Agent内存",
)
async def get_session_memory(
        session_id: str,
        memory_service: MemoryService = Depends(get_memory_service),
) -> Response[SessionMemoryResponse]:
    memories = await memory_service.get_session_memories(session_id)
    return Response.success(
        data=SessionMemoryResponse(
            planner=memories.get("planner", []),
            react=memories.get("react", []),
        )
    )


@router.post(
    path="/{session_id}/memory/compact",
    response_model=Response[Optional[Dict]],
    summary="压缩会话Agent内存",
)
async def compact_session_memory(
        session_id: str,
        request: ClearMemoryRequest,
        memory_service: MemoryService = Depends(get_memory_service),
) -> Response[Optional[Dict]]:
    await memory_service.compact_session_memory(session_id, request.agent_name)
    return Response.success(msg="压缩记忆成功")


@router.post(
    path="/{session_id}/memory/clear",
    response_model=Response[Optional[Dict]],
    summary="清空会话Agent内存",
)
async def clear_session_memory(
        session_id: str,
        request: ClearMemoryRequest,
        memory_service: MemoryService = Depends(get_memory_service),
) -> Response[Optional[Dict]]:
    await memory_service.clear_session_memory(session_id, request.agent_name)
    return Response.success(msg="清空记忆成功")


@router.delete(
    path="/{session_id}/memory/{agent_name}/messages/{index}",
    response_model=Response[Optional[Dict]],
    summary="删除会话内存中的指定消息",
)
async def delete_session_memory_message(
        session_id: str,
        agent_name: str,
        index: int,
        memory_service: MemoryService = Depends(get_memory_service),
) -> Response[Optional[Dict]]:
    await memory_service.delete_session_memory_message(session_id, agent_name, index)
    return Response.success(msg="删除消息成功")


@router.get(
    path="/{session_id}/checkpoints",
    response_model=Response[ListCheckpointsResponse],
    summary="获取会话还原点列表",
)
async def list_session_checkpoints(
        session_id: str,
        agent_service: AgentService = Depends(get_agent_service),
) -> Response[ListCheckpointsResponse]:
    checkpoints = await agent_service.list_checkpoints(session_id)
    return Response.success(
        msg="获取还原点列表成功",
        data=ListCheckpointsResponse(
            checkpoints=[
                CheckpointItemResponse(
                    id=item.id,
                    session_id=item.session_id,
                    anchor_type=item.anchor_type,
                    anchor_event_id=item.anchor_event_id,
                    label=item.label,
                    created_at=item.created_at,
                )
                for item in checkpoints
            ]
        ),
    )


@router.post(
    path="/{session_id}/checkpoints/{checkpoint_id}/restore",
    response_model=Response[RestoreCheckpointResponse],
    summary="回退到指定还原点",
)
async def restore_session_checkpoint(
        session_id: str,
        checkpoint_id: str,
        agent_service: AgentService = Depends(get_agent_service),
) -> Response[RestoreCheckpointResponse]:
    await agent_service.restore_checkpoint(session_id, checkpoint_id)
    return Response.success(
        msg="回退成功",
        data=RestoreCheckpointResponse(),
    )


@router.post(
    path="/{session_id}/stop",
    response_model=Response[Optional[Dict]],
    summary="停止指定任务会话",
    description="根据传递的指定会话id停止对应任务会话",
)
async def stop_session(
        session_id: str,
        agent_service: AgentService = Depends(get_agent_service),
) -> Response[Optional[Dict]]:
    """根据传递的指定会话id停止对应任务会话"""
    await agent_service.stop_session(session_id)
    return Response.success(msg="停止任务会话成功")


@router.get(
    path="/{session_id}/files",
    response_model=Response[GetSessionFilesResponse],
    summary="获取指定任务会话文件列表信息",
    description="获取指定任务会话文件列表信息",
)
async def get_session_files(
        session_id: str,
        session_service: SessionService = Depends(get_session_service),
) -> Response[GetSessionFilesResponse]:
    """获取指定任务会话文件列表信息"""
    files = await session_service.get_session_files(session_id)
    return Response.success(
        msg="获取会话文件列表成功",
        data=GetSessionFilesResponse(files=files)
    )


@router.post(
    path="/{session_id}/file",
    response_model=Response[FileReadResponse],
    summary="查看会话沙箱中指定文件的内容",
    description="根据传递的会话id+文件路径查看沙箱中文件的内容信息"
)
async def read_file(
        session_id: str,
        request: FileReadRequest,
        session_service: SessionService = Depends(get_session_service),
) -> Response[FileReadResponse]:
    """根据传递的会话id+文件路径查看沙箱中文件的内容信息"""
    result = await session_service.read_file(session_id, request.filepath)
    return Response.success(
        msg="获取会话文件内容成功",
        data=result
    )


@router.post(
    path="/{session_id}/shell",
    response_model=Response[ShellReadResponse],
    summary="查看会话的shell内容输出",
    description="传递指定会话id与shell会话标识，查看shell内容输出",
)
async def read_shell_output(
        session_id: str,
        request: ShellReadRequest,
        session_service: SessionService = Depends(get_session_service),
) -> Response[ShellReadResponse]:
    """查看会话的shell内容输出"""
    result = await session_service.read_shell_output(session_id, request.session_id)
    return Response.success(
        msg="获取Shell内容输出结果成功",
        data=result,
    )


@router.websocket(
    path="/{session_id}/vnc",
)
async def vnc_websocket(
        websocket: WebSocket,
        session_id: str,
        session_service: SessionService = Depends(get_session_service),
) -> None:
    """VNC Websocket端点，用于建立与沙箱环境的vnc连接，并双向转发数据"""
    # 1.从客户端noVNC接收子协议
    protocols_str = websocket.headers.get("sec-websocket-protocol", "")
    protocols = [p.strip() for p in protocols_str.split(",")]

    # 2.判断使用不同协议(noVNC首选binary)
    selected_protocol = None
    if "binary" in protocols:
        selected_protocol = "binary"
    elif "base64" in protocols:
        selected_protocol = "base64"

    # 3.使用对应协议接收websocket连接
    logger.info(f"为会话[{session_id}]开启WebSocket连接")
    await websocket.accept(subprotocol=selected_protocol)

    try:
        # 4.获取对应会话的vnc链接
        sandbox_vnc_url = await session_service.get_vnc_url(session_id)
        logger.info(f"连接WebSocket VNC： {sandbox_vnc_url}")

        # 5.创建上下文并连接到vnc
        async with websockets.connect(sandbox_vnc_url) as sandbox_ws:
            # 6.创建两个异步协程来完成数据的双向转发
            async def forward_to_sandbox():
                try:
                    while True:
                        # 接收来自客户端的数据
                        data = await websocket.receive_bytes()
                        await sandbox_ws.send(data)
                except WebSocketDisconnect:
                    logger.info(f"Web->VNC连接终端")
                except Exception as forward_e:
                    logger.error(f"forward_to_sandbox出错: {str(forward_e)}")

            async def forward_from_sandbox():
                try:
                    while True:
                        # 接收来自沙箱的数据并转发
                        data = await sandbox_ws.recv()
                        await websocket.send_bytes(data)
                except ConnectionClosed:
                    logger.info("VNC->Web连接关闭")
                except Exception as forward_e:
                    logger.error(f"forward_from_sandbox出错: {str(forward_e)}")

            # 7.并行运行两个任务
            forward_task1 = asyncio.create_task(forward_to_sandbox())
            forward_task2 = asyncio.create_task(forward_from_sandbox())

            # 8.等待任意任务结束意味WebSocket连接终端
            done, pending = await asyncio.wait(
                [forward_task1, forward_task2],
                return_when=asyncio.FIRST_COMPLETED,
            )
            logger.info("WebSocket连接已关闭")

            # 9.如果任一任务完成则取消其他任务(关闭全部链接)
            for task in pending:
                task.cancel()
    except ConnectionError as connection_e:
        # 连接沙箱环境失败，关闭websocket
        logger.error(f"连接沙箱环境失败: {str(connection_e)}")
        await websocket.close(code=1011, reason=f"连接沙箱环境失败: {str(connection_e)}")
    except Exception as e:
        # 其他错误记录日志并关闭websocket
        logger.error(f"WebSocket异常: {str(e)}")
        await websocket.close(code=1011, reason=f"WebSocket异常: {str(e)}")
