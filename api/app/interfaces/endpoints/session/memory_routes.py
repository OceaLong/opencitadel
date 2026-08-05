#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 本模块路由经 session_routes.py 的 .routes.extend() 聚合，勿在别处单独 include_router。
from typing import Dict, Optional

from fastapi import APIRouter, Depends

from app.application.errors.exceptions import NotFoundError
from app.application.services.memory_service import MemoryService
from app.application.services.session_service import SessionService
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.schemas import Response
from app.interfaces.schemas.memory import ClearMemoryRequest, SessionMemoryResponse
from app.interfaces.service_dependencies import get_memory_service, get_session_service

router = APIRouter(prefix="/sessions", tags=["会话模块"])


@router.get(
    path="/{session_id}/memory",
    response_model=Response[SessionMemoryResponse],
    summary="获取会话Agent内存",
)
async def get_session_memory(
        session_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        session_service: SessionService = Depends(get_session_service),
        memory_service: MemoryService = Depends(get_memory_service),
) -> Response[SessionMemoryResponse]:
    if not await session_service.get_session(session_id, scope=ctx.scope):
        raise NotFoundError("该会话不存在，请核实后重试")
    memories = await memory_service.get_session_memories(session_id)
    return Response.success(data=SessionMemoryResponse(
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
        ctx: WorkspaceContext = Depends(get_workspace_context),
        session_service: SessionService = Depends(get_session_service),
        memory_service: MemoryService = Depends(get_memory_service),
) -> Response[Optional[Dict]]:
    if not await session_service.get_session(session_id, scope=ctx.scope):
        raise NotFoundError("该会话不存在，请核实后重试")
    await memory_service.compact_session_memory(session_id, request.agent_name)
    return Response.success()


@router.post(
    path="/{session_id}/memory/clear",
    response_model=Response[Optional[Dict]],
    summary="清空会话Agent内存",
)
async def clear_session_memory(
        session_id: str,
        request: ClearMemoryRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        session_service: SessionService = Depends(get_session_service),
        memory_service: MemoryService = Depends(get_memory_service),
) -> Response[Optional[Dict]]:
    if not await session_service.get_session(session_id, scope=ctx.scope):
        raise NotFoundError("该会话不存在，请核实后重试")
    await memory_service.clear_session_memory(session_id, request.agent_name)
    return Response.success()


@router.delete(
    path="/{session_id}/memory/{agent_name}/messages/{index}",
    response_model=Response[Optional[Dict]],
    summary="删除会话内存中的指定消息",
)
async def delete_session_memory_message(
        session_id: str,
        agent_name: str,
        index: int,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        session_service: SessionService = Depends(get_session_service),
        memory_service: MemoryService = Depends(get_memory_service),
) -> Response[Optional[Dict]]:
    if not await session_service.get_session(session_id, scope=ctx.scope):
        raise NotFoundError("该会话不存在，请核实后重试")
    await memory_service.delete_session_memory_message(session_id, agent_name, index)
    return Response.success()
