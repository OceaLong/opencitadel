import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, Query, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.application.ports.streams import SessionListStreamFactory
from app.application.services.agent_service import AgentService
from app.application.services.audit_service import AuditService
from app.application.services.inference_model_service import InferenceModelService
from app.application.services.llm_token_usage_service import LLMTokenUsageService
from app.application.services.quota_service import QuotaService
from app.application.services.runtime_policy_reader import RuntimePolicyReader
from app.application.services.session_service import SessionService
from app.application.services.skill_service import SkillService
from app.domain.errors import NotFoundError
from app.domain.models.audit_log import AuditLog
from app.domain.models.scope import OwnerScopeType, Principal, WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context, require_non_auditor
from app.interfaces.client_ip import get_client_ip
from app.interfaces.endpoints.session import (
    sandbox_routes,
)
from app.interfaces.endpoints.session._helpers import (
    _session_to_list_item,
    build_get_session_response,
    session_stream_interval_seconds,
)
from app.interfaces.schemas import Response
from app.interfaces.schemas.session import (
    ChatRequest,
    CreateSessionRequest,
    CreateSessionResponse,
    ExecutionEventResponse,
    GetSessionEventsResponse,
    GetSessionResponse,
    GetSessionTokenUsageResponse,
    ListSessionResponse,
    TokenUsageRecordResponse,
    TokenUsageSummaryResponse,
    UpdateSessionConfigRequest,
)
from app.interfaces.service_dependencies import (
    get_agent_service,
    get_audit_service,
    get_inference_model_service,
    get_llm_token_usage_service,
    get_quota_service,
    get_runtime_policy_reader,
    get_session_list_stream_factory,
    get_session_service,
    get_skill_service,
)
from app.interfaces.streaming import finish_snapshot_before_cancellation

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
    # 会话准入：日会话数 + 月 Token + 并发任务数（个人配额；team scope 时同时校验团队配额）。
    await quota_service.check_session_quota(ctx.principal.user_id, scope=ctx.scope)
    session = await session_service.create_session(
        title=request.title or "新对话",
        model_id=request.model_id,
        skill_id=request.skill_id,
        thinking_enabled=bool(request.thinking_enabled)
        if request.thinking_enabled is not None
        else False,
        knowledge_base_id=request.knowledge_base_id,
        knowledge_base_version_id=request.knowledge_base_version_id,
        mode=request.mode,
        operator_scope=request.operator_scope,
        operator_domains=request.operator_domains,
        scope=ctx.scope,
    )
    if request.operator_scope:
        await audit_service.record(
            AuditLog(
                actor_user_id=ctx.principal.user_id,
                actor_ip=get_client_ip(http_request) if http_request else "",
                action="operator_scope_declared",
                resource_type="session",
                resource_id=session.id,
                team_id=ctx.scope.team_id if ctx.scope.type == OwnerScopeType.TEAM else None,
                session_id=session.id,
                metadata={
                    "ownership": request.operator_scope,
                    "operator_domains": request.operator_domains,
                },
            )
        )
    return Response.success(data=CreateSessionResponse(session_id=session.id))


@router.post(
    path="/stream",
    summary="流式获取所有会话基础信息列表",
    description="间隔指定时间流式获取所有会话基础信息列表",
)
async def stream_sessions(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, description="按标题/最新消息关键词过滤会话"),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session_service: SessionService = Depends(get_session_service),
    policy_reader: RuntimePolicyReader = Depends(get_runtime_policy_reader),
    streams: SessionListStreamFactory = Depends(get_session_list_stream_factory),
) -> EventSourceResponse:
    """间隔指定时间流式获取所有会话基础信息列表"""

    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        """Push session list updates on Redis pub/sub; heartbeat on idle timeout."""

        async def build_sessions_event() -> ServerSentEvent:
            sessions = await finish_snapshot_before_cancellation(
                session_service.get_all_sessions(
                    limit=limit, offset=offset, scope=ctx.scope, search=q
                )
            )
            session_items = [_session_to_list_item(session) for session in sessions]
            return ServerSentEvent(
                event="sessions",
                data=ListSessionResponse(sessions=session_items).model_dump_json(),
            )

        yield await build_sessions_event()

        try:
            async with streams.open() as stream:
                while True:
                    wait_seconds = await session_stream_interval_seconds(policy_reader)
                    poll = await stream.poll(timeout_seconds=float(wait_seconds))
                    if not poll.connectivity.available:
                        await asyncio.sleep(wait_seconds)
                    yield await build_sessions_event()
        except (OSError, RuntimeError, ValueError):
            while True:
                wait_seconds = await session_stream_interval_seconds(policy_reader)
                await asyncio.sleep(wait_seconds)
                yield await build_sessions_event()

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
    q: str | None = Query(default=None, description="按标题/最新消息关键词过滤会话"),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session_service: SessionService = Depends(get_session_service),
) -> Response[ListSessionResponse]:
    """获取 OpenCitadel 项目中所有任务会话基础信息列表"""
    sessions = await session_service.get_all_sessions(
        limit=limit, offset=offset, scope=ctx.scope, search=q
    )
    session_items = [_session_to_list_item(session) for session in sessions]
    return Response.success(data=ListSessionResponse(sessions=session_items))


# 回收站列表必须在 ``GET /{session_id}`` 之前注册，避免 "deleted" 被当作会话 id。
@router.get(
    path="/deleted",
    response_model=Response[ListSessionResponse],
    summary="获取回收站会话列表",
    description="获取当前工作区内已软删除、可恢复的任务会话列表",
)
async def list_deleted_sessions(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session_service: SessionService = Depends(get_session_service),
) -> Response[ListSessionResponse]:
    """获取回收站（已软删除）会话列表"""
    sessions = await session_service.list_deleted_sessions(
        limit=limit, offset=offset, scope=ctx.scope
    )
    session_items = [_session_to_list_item(session) for session in sessions]
    return Response.success(data=ListSessionResponse(sessions=session_items))


@router.post(
    path="/{session_id}/clear-unread-message-count",
    response_model=Response[dict | None],
    summary="清除指定任务会话未读消息数",
    description="清除指定任务会话未读消息数",
)
async def clear_unread_message_count(
    session_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session_service: SessionService = Depends(get_session_service),
) -> Response[dict | None]:
    """根据传递的会话id清空未读消息数"""
    if not await session_service.get_session(session_id, scope=ctx.scope):
        raise NotFoundError("该会话不存在，请核实后重试")
    await session_service.clear_unread_message_count(session_id)
    return Response.success()


@router.post(
    path="/{session_id}/delete",
    response_model=Response[dict | None],
    summary="删除指定任务会话（软删除，进入回收站）",
    description="根据传递的会话id软删除任务会话；记录进入回收站，可恢复",
)
async def delete_session(
    session_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session_service: SessionService = Depends(get_session_service),
) -> Response[dict | None]:
    """根据传递的会话id软删除指定任务会话（进入回收站）"""
    await session_service.delete_session(session_id, scope=ctx.scope)
    return Response.success()


@router.post(
    path="/{session_id}/restore",
    response_model=Response[dict | None],
    summary="从回收站恢复任务会话",
    description="根据传递的会话id恢复已软删除的任务会话",
)
async def restore_session(
    session_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session_service: SessionService = Depends(get_session_service),
) -> Response[dict | None]:
    """根据传递的会话id从回收站恢复任务会话"""
    await session_service.restore_session(session_id, scope=ctx.scope)
    return Response.success()


@router.post(
    path="/{session_id}/purge",
    response_model=Response[dict | None],
    summary="彻底清除回收站中的任务会话",
    description="根据传递的会话id物理删除回收站中的任务会话（不可恢复）",
)
async def purge_session(
    session_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session_service: SessionService = Depends(get_session_service),
) -> Response[dict | None]:
    """根据传递的会话id彻底清除回收站中的任务会话"""
    await session_service.purge_session(session_id, scope=ctx.scope)
    return Response.success()


@router.post(
    path="/{session_id}/chat",
    summary="向指定任务会话发起聊天请求",
    description="向指定任务会话发起聊天请求",
)
async def chat(
    session_id: str,
    request: ChatRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard: Principal = Depends(require_non_auditor),
    agent_service: AgentService = Depends(get_agent_service),
    session_service: SessionService = Depends(get_session_service),
) -> EventSourceResponse:
    """根据传递的会话id+chat请求数据向指定会话发起聊天请求"""
    session = await session_service.get_session(session_id, scope=ctx.scope)
    if not session:
        raise NotFoundError("该会话不存在，请核实后重试")

    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        """定义事件生成器，用于配合EventSourceResponse生成流式响应数据"""
        async for event in agent_service.chat(
            session_id=session_id,
            owner_scope=ctx.scope,
            message=request.message,
            request_id=request.request_id,
            attachments=request.attachments,
            latest_event_id=request.event_id,
            timestamp=(
                datetime.fromtimestamp(request.timestamp, tz=UTC) if request.timestamp else None
            ),
            model_id=request.model_id,
            skill_id=request.skill_id,
            thinking_enabled=request.thinking_enabled,
            mode=request.mode,
        ):
            yield ServerSentEvent(
                id=event.cursor,
                event=event.event_type,
                data=json.dumps(event.payload, ensure_ascii=False),
            )

    return EventSourceResponse(event_generator())


@router.get(
    path="/{session_id}/events",
    response_model=Response[GetSessionEventsResponse],
    summary="分页获取指定会话事件",
    description="根据游标分页获取指定会话的持久化事件",
)
async def get_execution_events(
    session_id: str,
    after: str | None = Query(default=None),
    before: str | None = Query(default=None),
    latest: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session_service: SessionService = Depends(get_session_service),
    agent_service: AgentService = Depends(get_agent_service),
) -> Response[GetSessionEventsResponse]:
    if not await session_service.get_session(session_id, scope=ctx.scope):
        raise NotFoundError("该会话不存在，请核实后重试")
    page = await agent_service.list_events(
        session_id,
        owner_scope=ctx.scope,
        after=after,
        before=before,
        limit=limit,
        latest=latest,
    )
    return Response.success(
        data=GetSessionEventsResponse(
            events=[
                ExecutionEventResponse(**event.model_dump(mode="python")) for event in page.events
            ],
            next_cursor=page.next_cursor,
            prev_cursor=page.prev_cursor,
            has_earlier=page.has_earlier,
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
    events_limit: int = Query(default=100, ge=1, le=500),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session_service: SessionService = Depends(get_session_service),
    inference_model_service: InferenceModelService = Depends(get_inference_model_service),
    skill_service: SkillService = Depends(get_skill_service),
    token_usage_service: LLMTokenUsageService = Depends(get_llm_token_usage_service),
    agent_service: AgentService = Depends(get_agent_service),
) -> Response[GetSessionResponse]:
    """传递指定会话id获取该会话的对话详情"""
    session = await session_service.get_session(session_id, scope=ctx.scope)
    if not session:
        raise NotFoundError("该会话不存在，请核实后重试")
    event_page = await agent_service.list_events(
        session_id,
        owner_scope=ctx.scope,
        latest=True,
        limit=events_limit,
    )
    return Response.success(
        data=await build_get_session_response(
            session,
            inference_model_service,
            skill_service,
            ctx.scope,
            token_usage_service,
            event_page=event_page,
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
    inference_model_service: InferenceModelService = Depends(get_inference_model_service),
    token_usage_service: LLMTokenUsageService = Depends(get_llm_token_usage_service),
) -> Response[GetSessionTokenUsageResponse]:
    session = await session_service.get_session(session_id, scope=ctx.scope)
    if not session:
        raise NotFoundError("该会话不存在，请核实后重试")
    model_prices = {}
    if session.model_id:
        try:
            model = await inference_model_service.get_model(
                session.model_id,
                scope=ctx.scope,
            )
            model_prices[model.id] = (model.input_price_per_million, model.output_price_per_million)
            model_prices[model.model_name] = (
                model.input_price_per_million,
                model.output_price_per_million,
            )
        except (OSError, RuntimeError, ValueError):
            pass
    summary = await token_usage_service.get_session_summary(
        session_id, model_prices=model_prices or None
    )
    records = await token_usage_service.list_by_session(session_id)
    return Response.success(
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
    events_limit: int = Query(default=100, ge=1, le=500),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard: Principal = Depends(require_non_auditor),
    session_service: SessionService = Depends(get_session_service),
    inference_model_service: InferenceModelService = Depends(get_inference_model_service),
    skill_service: SkillService = Depends(get_skill_service),
    token_usage_service: LLMTokenUsageService = Depends(get_llm_token_usage_service),
    agent_service: AgentService = Depends(get_agent_service),
) -> Response[GetSessionResponse]:
    await session_service.update_session_config(
        session_id,
        model_id=request.model_id,
        skill_id=request.skill_id,
        thinking_enabled=request.thinking_enabled,
        operator_domains=request.operator_domains,
        scope=ctx.scope,
    )
    session = await session_service.get_session(session_id, scope=ctx.scope)
    if not session:
        raise NotFoundError("该会话不存在，请核实后重试")
    event_page = await agent_service.list_events(
        session_id,
        owner_scope=ctx.scope,
        latest=True,
        limit=events_limit,
    )
    return Response.success(
        data=await build_get_session_response(
            session,
            inference_model_service,
            skill_service,
            ctx.scope,
            token_usage_service,
            event_page=event_page,
        ),
    )


@router.post(
    path="/{session_id}/stop",
    response_model=Response[dict | None],
    summary="停止指定任务会话",
    description="根据传递的指定会话id停止对应任务会话",
)
async def stop_session(
    session_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard: Principal = Depends(require_non_auditor),
    session_service: SessionService = Depends(get_session_service),
    agent_service: AgentService = Depends(get_agent_service),
) -> Response[dict | None]:
    """根据传递的指定会话id停止对应任务会话"""
    if not await session_service.get_session(session_id, scope=ctx.scope):
        raise NotFoundError("该会话不存在，请核实后重试")
    await agent_service.stop_session(session_id, owner_scope=ctx.scope)
    return Response.success()


# 沙箱（文件/终端/VNC）子路由保留真实 APIRoute 对象，便于安全契约直接
# 检查每个路由的依赖；外层再次 include_router 时 HTTP 分发行为不变。
for _sub_router_module in (sandbox_routes,):
    router.routes.extend(_sub_router_module.router.routes)
