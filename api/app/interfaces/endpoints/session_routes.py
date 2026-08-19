#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Optional, Dict, AsyncGenerator

from fastapi import APIRouter, Depends, Body, Query, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.domain.errors import NotFoundError
from app.application.services.agent_service import AgentService
from app.application.services.session_service import SessionService
from app.interfaces.client_ip import get_client_ip
from app.interfaces.schemas import Response
from app.interfaces.schemas.event import EventMapper
from app.interfaces.schemas.session import (
    CreateSessionRequest,
    CreateSessionResponse,
    ListSessionResponse,
    ChatRequest,
    GetSessionResponse,
    UpdateSessionConfigRequest,
    GetSessionTokenUsageResponse,
    TokenUsageSummaryResponse,
    TokenUsageRecordResponse,
    GetSessionEventsResponse,
)
from app.interfaces.service_dependencies import (
    get_session_service,
    get_agent_service,
    get_llm_model_service,
    get_skill_service,
    get_llm_token_usage_service,
    get_quota_service,
    get_audit_service,
)
from app.application.services.quota_service import QuotaService
from app.application.services.audit_service import AuditService
from app.domain.models.audit_log import AuditLog
from app.interfaces.auth_dependencies import get_workspace_context, require_non_auditor
from app.application.services.llm_token_usage_service import LLMTokenUsageService
from app.application.services.llm_model_service import LLMModelService
from app.application.services.skill_service import SkillService
from app.domain.models.scope import OwnerScopeType, Principal, WorkspaceContext
from app.domain.models.event_policy import should_project_event
from app.interfaces.endpoints.session import (
    approval_routes,
    checkpoint_routes,
    memory_routes,
    sandbox_routes,
)
from app.interfaces.endpoints.session._helpers import (
    SESSION_SLEEP_INTERVAL,
    _format_clarify_answers,
    _record_gate_audit_if_needed,
    _session_to_list_item,
    build_get_session_response,
)

router = APIRouter(prefix="/sessions", tags=["会话模块"])


@router.post(
    path="",
    response_model=Response[CreateSessionResponse],
    summary="创建新任务会话",
    description="创建一个空白的新任务会话",
)
async def create_session(
        http_request: Request,
        request: CreateSessionRequest = Body(default_factory=CreateSessionRequest),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        _write_guard: Principal = Depends(require_non_auditor),
        session_service: SessionService = Depends(get_session_service),
        quota_service: QuotaService = Depends(get_quota_service),
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[CreateSessionResponse]:
    """创建一个空白的新任务会话"""
    await quota_service.check_session_quota(ctx.principal.user_id)
    session = await session_service.create_session(
        title=request.title or "新对话",
        model_id=request.model_id,
        skill_id=request.skill_id,
        thinking_enabled=bool(request.thinking_enabled) if request.thinking_enabled is not None else False,
        codebase_id=request.codebase_id,
        codebase_version_id=request.codebase_version_id,
        knowledge_base_id=request.knowledge_base_id,
        knowledge_base_version_id=request.knowledge_base_version_id,
        mode=request.mode,
        operator_scope=request.operator_scope,
        operator_domains=request.operator_domains,
        gate_profile=request.gate_profile,
        scope=ctx.scope,
    )
    if request.operator_scope:
        await audit_service.record(AuditLog(
            actor_user_id=ctx.principal.user_id,
            actor_ip=get_client_ip(http_request) if http_request else "",
            action="operator_scope_declared",
            resource_type="session",
            resource_id=session.id,
            team_id=ctx.scope.team_id if ctx.scope.type == OwnerScopeType.TEAM else None,
            metadata={
                "ownership": request.operator_scope,
                "operator_domains": request.operator_domains,
                "gate_profile": request.gate_profile or "standard",
            },
        ))
    return Response.success(data=CreateSessionResponse(session_id=session.id))


@router.post(
    path="/stream",
    summary="流式获取所有会话基础信息列表",
    description="间隔指定时间流式获取所有会话基础信息列表",
)
async def stream_sessions(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        session_service: SessionService = Depends(get_session_service),
) -> EventSourceResponse:
    """间隔指定时间流式获取所有会话基础信息列表"""

    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        """Push session list updates on Redis pub/sub; heartbeat on idle timeout."""
        from app.infrastructure.external.session_list_notifier import SESSION_LIST_CHANNEL
        from app.infrastructure.storage.redis import get_redis

        async def build_sessions_event() -> ServerSentEvent:
            sessions = await session_service.get_all_sessions(limit=limit, offset=offset, scope=ctx.scope)
            session_items = [
                _session_to_list_item(session)
                for session in sessions
            ]
            return ServerSentEvent(
                event="sessions",
                data=ListSessionResponse(sessions=session_items).model_dump_json(),
            )

        yield await build_sessions_event()

        pubsub = get_redis().client.pubsub()
        await pubsub.subscribe(SESSION_LIST_CHANNEL)
        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=float(SESSION_SLEEP_INTERVAL),
                )
                if message and message.get("type") == "message":
                    yield await build_sessions_event()
                else:
                    yield ServerSentEvent(event="ping", data="")
        finally:
            await pubsub.unsubscribe(SESSION_LIST_CHANNEL)
            await pubsub.aclose()

    return EventSourceResponse(event_generator())


@router.get(
    path="",
    response_model=Response[ListSessionResponse],
    summary="获取会话列表基础信息",
    description="获取 OpenCitadel 项目中所有任务会话基础信息列表",
)
async def get_all_sessions(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        session_service: SessionService = Depends(get_session_service),
) -> Response[ListSessionResponse]:
    """获取 OpenCitadel 项目中所有任务会话基础信息列表"""
    sessions = await session_service.get_all_sessions(limit=limit, offset=offset, scope=ctx.scope)
    session_items = [
        _session_to_list_item(session)
        for session in sessions
    ]
    return Response.success(data=ListSessionResponse(sessions=session_items))


@router.post(
    path="/{session_id}/clear-unread-message-count",
    response_model=Response[Optional[Dict]],
    summary="清除指定任务会话未读消息数",
    description="清除指定任务会话未读消息数",
)
async def clear_unread_message_count(
        session_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        session_service: SessionService = Depends(get_session_service),
) -> Response[Optional[Dict]]:
    """根据传递的会话id清空未读消息数"""
    if not await session_service.get_session(session_id, scope=ctx.scope):
        raise NotFoundError("该会话不存在，请核实后重试")
    await session_service.clear_unread_message_count(session_id)
    return Response.success()


@router.post(
    path="/{session_id}/delete",
    response_model=Response[Optional[Dict]],
    summary="删除指定任务会话",
    description="根据传递的会话id删除指定任务会话",
)
async def delete_session(
        session_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        session_service: SessionService = Depends(get_session_service),
) -> Response[Optional[Dict]]:
    """根据传递的会话id删除指定任务会话"""
    await session_service.delete_session(session_id, scope=ctx.scope)
    return Response.success()


@router.post(
    path="/{session_id}/chat",
    summary="向指定任务会话发起聊天请求",
    description="向指定任务会话发起聊天请求"
)
async def chat(
        session_id: str,
        request: ChatRequest,
        http_request: Request,
        include_debug: bool = Query(default=False),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        _write_guard: Principal = Depends(require_non_auditor),
        agent_service: AgentService = Depends(get_agent_service),
        session_service: SessionService = Depends(get_session_service),
        audit_service: AuditService = Depends(get_audit_service),
) -> EventSourceResponse:
    """根据传递的会话id+chat请求数据向指定会话发起聊天请求"""
    session = await session_service.get_session(session_id, scope=ctx.scope)
    if not session:
        raise NotFoundError("该会话不存在，请核实后重试")

    message = _format_clarify_answers(request)
    await _record_gate_audit_if_needed(
        session=session,
        message=message,
        ctx=ctx,
        audit_service=audit_service,
        request=http_request,
    )

    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        """定义事件生成器，用于配合EventSourceResponse生成流式响应数据"""
        async for event in agent_service.chat(
                session_id=session_id,
                message=message,
                attachments=request.attachments,
                clarify_answers=request.clarify_answers,
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
        before: Optional[int] = Query(default=None),
        latest: bool = Query(default=False),
        limit: int = Query(default=100, ge=1, le=500),
        include_debug: bool = Query(default=False),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        session_service: SessionService = Depends(get_session_service),
) -> Response[GetSessionEventsResponse]:
    records = await session_service.get_session_events(
        session_id,
        after=after,
        before=before,
        limit=limit,
        latest=latest,
        scope=ctx.scope,
    )
    projected = [
        event
        for _, event in records
        if should_project_event(event, include_transient=False, include_debug=include_debug)
    ]
    prev_cursor = records[0][0] if records else None
    has_earlier = False
    if prev_cursor is not None:
        has_earlier = await session_service.has_events_before(session_id, prev_cursor)
    return Response.success(data=GetSessionEventsResponse(
            events=EventMapper.events_to_sse_events(projected, include_debug=include_debug),
            next_cursor=records[-1][0] if len(records) == limit and not latest and before is None else None,
            prev_cursor=prev_cursor,
            has_earlier=has_earlier,
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
        ctx: WorkspaceContext = Depends(get_workspace_context),
        session_service: SessionService = Depends(get_session_service),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
        skill_service: SkillService = Depends(get_skill_service),
        token_usage_service: LLMTokenUsageService = Depends(get_llm_token_usage_service),
) -> Response[GetSessionResponse]:
    """传递指定会话id获取该会话的对话详情"""
    session = await session_service.get_session(session_id, scope=ctx.scope)
    if not session:
        raise NotFoundError("该会话不存在，请核实后重试")
    event_records = await session_service.get_session_events(session_id, limit=events_limit, scope=ctx.scope)
    return Response.success(data=await build_get_session_response(
            session,
            llm_model_service,
            skill_service,
            ctx.scope,
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
        ctx: WorkspaceContext = Depends(get_workspace_context),
        session_service: SessionService = Depends(get_session_service),
        llm_model_service: LLMModelService = Depends(get_llm_model_service),
        token_usage_service: LLMTokenUsageService = Depends(get_llm_token_usage_service),
) -> Response[GetSessionTokenUsageResponse]:
    session = await session_service.get_session(session_id, scope=ctx.scope)
    if not session:
        raise NotFoundError("该会话不存在，请核实后重试")
    model_prices = {}
    if session.model_id:
        try:
            model = await llm_model_service.get_model(
                session.model_id,
                mask=False,
                scope=ctx.scope,
            )
            model_prices[model.id] = (model.input_price_per_million, model.output_price_per_million)
            model_prices[model.model_name] = (model.input_price_per_million, model.output_price_per_million)
        except Exception:
            pass
    summary = await token_usage_service.get_session_summary(session_id, model_prices=model_prices or None)
    records = await token_usage_service.list_by_session(session_id)
    return Response.success(data=GetSessionTokenUsageResponse(
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
        ctx: WorkspaceContext = Depends(get_workspace_context),
        _write_guard: Principal = Depends(require_non_auditor),
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
        gate_profile=request.gate_profile,
        operator_domains=request.operator_domains,
        scope=ctx.scope,
    )
    session = await session_service.get_session(session_id, scope=ctx.scope)
    if not session:
        raise NotFoundError("该会话不存在，请核实后重试")
    event_records = await session_service.get_session_events(session_id, limit=events_limit, scope=ctx.scope)
    return Response.success(data=await build_get_session_response(
            session,
            llm_model_service,
            skill_service,
            ctx.scope,
            token_usage_service,
            include_debug=include_debug,
            event_records=event_records,
            event_limit=events_limit,
        ),
    )


@router.post(
    path="/{session_id}/stop",
    response_model=Response[Optional[Dict]],
    summary="停止指定任务会话",
    description="根据传递的指定会话id停止对应任务会话",
)
async def stop_session(
        session_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        _write_guard: Principal = Depends(require_non_auditor),
        session_service: SessionService = Depends(get_session_service),
        agent_service: AgentService = Depends(get_agent_service),
) -> Response[Optional[Dict]]:
    """根据传递的指定会话id停止对应任务会话"""
    if not await session_service.get_session(session_id, scope=ctx.scope):
        raise NotFoundError("该会话不存在，请核实后重试")
    await agent_service.stop_session(session_id)
    return Response.success()


# 审批 / 记忆 / 还原点 / 沙箱(文件/终端/VNC) 路由拆分至 session 子包，行为保持逐字迁移。
# 子路由自带 prefix="/sessions" 并直接拼接真实 route 对象（而非 include_router），
# 因为当前 fastapi/starlette 版本下 include_router 会把整个子路由懒包装成
# _IncludedRouter 代理对象，使 router.routes 直接遍历（如
# test_remediation_governance_invariants.py 用 dependant/methods 校验网关依赖）看不到
# 其内部真实的 APIRoute；直接拼接 .routes 列表可保留真实 route 对象与其依赖，
# 同时对外层再次 include_router（真实 HTTP 分发）行为完全等价。
for _sub_router_module in (approval_routes, memory_routes, checkpoint_routes, sandbox_routes):
    router.routes.extend(_sub_router_module.router.routes)
