#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 本模块路由经 session_routes.py 的 .routes.extend() 聚合，勿在别处单独 include_router。
from fastapi import APIRouter, Depends, Request

from app.application.errors.exceptions import NotFoundError
from app.application.services.agent_service import AgentService
from app.application.services.audit_service import AuditService
from app.application.services.session_service import SessionService
from app.domain.models.audit_log import AuditLog
from app.domain.models.scope import OwnerScopeType, WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.client_ip import get_client_ip
from app.interfaces.schemas import Response
from app.interfaces.schemas.checkpoint import (
    CheckpointItemResponse,
    ListCheckpointsResponse,
    RestoreCheckpointResponse,
)
from app.interfaces.service_dependencies import (
    get_agent_service,
    get_audit_service,
    get_session_service,
)

router = APIRouter(prefix="/sessions", tags=["会话模块"])


@router.get(
    path="/{session_id}/checkpoints",
    response_model=Response[ListCheckpointsResponse],
    summary="获取会话还原点列表",
)
async def list_session_checkpoints(
        session_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        session_service: SessionService = Depends(get_session_service),
        agent_service: AgentService = Depends(get_agent_service),
) -> Response[ListCheckpointsResponse]:
    if not await session_service.get_session(session_id, scope=ctx.scope):
        raise NotFoundError("该会话不存在，请核实后重试")
    checkpoints = await agent_service.list_checkpoints(session_id)
    return Response.success(data=ListCheckpointsResponse(
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
        http_request: Request,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        session_service: SessionService = Depends(get_session_service),
        agent_service: AgentService = Depends(get_agent_service),
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[RestoreCheckpointResponse]:
    session = await session_service.get_session(session_id, scope=ctx.scope)
    if not session:
        raise NotFoundError("该会话不存在，请核实后重试")
    await agent_service.restore_checkpoint(session_id, checkpoint_id)
    await audit_service.record(AuditLog(
        actor_user_id=ctx.principal.user_id,
        actor_ip=get_client_ip(http_request),
        action="agent_rollback",
        resource_type="session",
        resource_id=session_id,
        team_id=ctx.scope.team_id if ctx.scope.type == OwnerScopeType.TEAM else None,
        metadata={"checkpoint_id": checkpoint_id, "operator_scope": session.operator_scope},
    ))
    return Response.success(data=RestoreCheckpointResponse(),
    )
