#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Body, Query

from app.application.services.app_config_service import AppConfigService
from app.domain.models.app_config import AgentConfig, MCPConfig, MCPTransport
from app.domain.models.app_config_scope import ALL_APP_CONFIG_SECTIONS, GLOBAL_ONLY_SECTIONS, USER_OVERRIDABLE_SECTIONS
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context, require_admin
from app.interfaces.schemas.app_config import ListMCPServerResponse, ListA2AServerResponse
from app.interfaces.schemas.base import Response
from app.interfaces.service_dependencies import get_app_config_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/app-config", tags=["设置模块"])


@router.get(
    path="/sections",
    response_model=Response[list[str]],
    summary="列出可管理的配置段",
)
async def list_sections() -> Response[list[str]]:
    return Response.success(data=sorted(ALL_APP_CONFIG_SECTIONS))


@router.get(
    path="/sections/{section}",
    response_model=Response[Dict[str, Any]],
    summary="获取指定配置段",
)
async def get_section(
        section: str,
        use_user_override: bool = Query(False),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[Dict[str, Any]]:
    if section in GLOBAL_ONLY_SECTIONS and not ctx.principal.is_admin:
        from app.application.errors.exceptions import ForbiddenError
        raise ForbiddenError("仅管理员可查看该配置段")
    data = await app_config_service.get_section(
        section,
        scope=ctx.scope,
        use_user_override=use_user_override,
    )
    return Response.success(data=data.model_dump(mode="json"))


@router.put(
    path="/sections/{section}",
    response_model=Response[Dict[str, Any]],
    summary="更新指定配置段",
)
async def update_section(
        section: str,
        payload: Dict[str, Any],
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[Dict[str, Any]]:
    is_admin = ctx.principal.is_admin
    if section not in USER_OVERRIDABLE_SECTIONS and not is_admin:
        from app.application.errors.exceptions import ForbiddenError
        raise ForbiddenError("仅管理员可修改该配置段")
    updated = await app_config_service.update_section(
        section,
        payload,
        changed_by=ctx.principal.user_id,
        scope=ctx.scope,
        is_admin=is_admin,
    )
    return Response.success( data=updated.model_dump(mode="json"))


@router.delete(
    path="/user-override",
    response_model=Response[Optional[Dict]],
    summary="清除当前用户的配置覆盖",
)
async def delete_user_override(
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[Optional[Dict]]:
    await app_config_service.delete_user_override(ctx.principal.user_id, changed_by=ctx.principal.user_id)
    return Response.success()


@router.get(
    path="/revisions",
    response_model=Response[list[Dict[str, Any]]],
    summary="配置版本历史",
)
async def list_revisions(
        scope: Optional[str] = Query(None),
        owner_user_id: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[list[Dict[str, Any]]]:
    if scope is None and owner_user_id is None:
        if ctx.principal.is_admin:
            scope = "global"
        else:
            owner_user_id = ctx.principal.user_id
            scope = "user"
    if owner_user_id and owner_user_id != ctx.principal.user_id and not ctx.principal.is_admin:
        from app.application.errors.exceptions import ForbiddenError
        raise ForbiddenError("无权查看其他用户的配置历史")
    if scope == "user" and not owner_user_id:
        owner_user_id = ctx.principal.user_id
    revisions = await app_config_service.list_revisions(
        scope=scope,
        owner_user_id=owner_user_id,
        limit=limit,
        offset=offset,
    )
    return Response.success(data=[r.model_dump(mode="json") for r in revisions])


@router.post(
    path="/revisions/{revision_id}/rollback",
    response_model=Response[Dict[str, Any]],
    summary="回滚到指定配置版本",
)
async def rollback_revision(
        revision_id: str,
        _admin=Depends(require_admin),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[Dict[str, Any]]:
    restored = await app_config_service.rollback_revision(
        revision_id,
        changed_by=ctx.principal.user_id,
    )
    return Response.success( data=restored.model_dump(mode="json"))


@router.get(
    path="/agent",
    response_model=Response[AgentConfig],
    summary="获取Agent通用配置信息",
)
async def get_agent_config(
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[AgentConfig]:
    agent_config = await app_config_service.get_agent_config(scope=ctx.scope)
    return Response.success(data=agent_config.model_dump())


@router.post(
    path="/agent",
    response_model=Response[AgentConfig],
    summary="更新Agent通用配置信息",
)
async def update_agent_config(
        new_agent_config: AgentConfig,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[AgentConfig]:
    updated = await app_config_service.update_agent_config(
        new_agent_config,
        changed_by=ctx.principal.user_id,
        scope=ctx.scope,
        is_admin=ctx.principal.is_admin,
    )
    return Response.success( data=updated.model_dump())


@router.get(
    path="/mcp-servers",
    response_model=Response[ListMCPServerResponse],
    summary="获取MCP服务器工具列表",
)
async def get_mcp_servers(
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[ListMCPServerResponse]:
    mcp_servers = await app_config_service.get_mcp_servers(scope=ctx.scope)
    return Response.success( data=ListMCPServerResponse(mcp_servers=mcp_servers))


@router.post(
    path="/mcp-servers",
    response_model=Response[Optional[Dict]],
    summary="新增MCP服务配置",
)
async def create_mcp_servers(
        mcp_config: MCPConfig,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[Optional[Dict]]:
    if not ctx.principal.is_admin:
        for cfg in mcp_config.mcpServers.values():
            if cfg.transport == MCPTransport.STDIO:
                from app.application.errors.exceptions import ForbiddenError
                raise ForbiddenError("仅管理员可配置 stdio 类型的 MCP 服务")
    await app_config_service.update_and_create_mcp_servers(
        mcp_config,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
        is_admin=ctx.principal.is_admin,
    )
    return Response.success()


@router.post(
    path="/mcp-servers/{server_name}/update",
    response_model=Response[Optional[Dict]],
    summary="更新MCP服务配置",
)
async def update_mcp_server(
        server_name: str,
        mcp_config: MCPConfig,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[Optional[Dict]]:
    if not ctx.principal.is_admin:
        cfg = mcp_config.mcpServers.get(server_name)
        if cfg and cfg.transport == MCPTransport.STDIO:
            from app.application.errors.exceptions import ForbiddenError
            raise ForbiddenError("仅管理员可配置 stdio 类型的 MCP 服务")
    await app_config_service.update_mcp_server(
        server_name,
        mcp_config,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
        is_admin=ctx.principal.is_admin,
    )
    return Response.success()


@router.post(
    path="/mcp-servers/{server_name}/delete",
    response_model=Response[Optional[Dict]],
    summary="删除MCP服务配置",
)
async def delete_mcp_server(
        server_name: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[Optional[Dict]]:
    await app_config_service.delete_mcp_server(
        server_name,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
    )
    return Response.success()


@router.post(
    path="/mcp-servers/{server_name}/enabled",
    response_model=Response[Optional[Dict]],
    summary="更新MCP服务的启用状态",
)
async def set_mcp_server_enabled(
        server_name: str,
        enabled: bool = Body(..., embed=True),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[Optional[Dict]]:
    await app_config_service.set_mcp_server_enabled(
        server_name,
        enabled,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
    )
    return Response.success()


@router.get(
    path="/a2a-servers",
    response_model=Response[ListA2AServerResponse],
    summary="获取a2a服务器列表",
)
async def get_a2a_servers(
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[ListA2AServerResponse]:
    a2a_servers = await app_config_service.get_a2a_servers(scope=ctx.scope)
    return Response.success( data=ListA2AServerResponse(a2a_servers=a2a_servers))


@router.post(
    path="/a2a-servers",
    response_model=Response[Optional[Dict]],
    summary="新增a2a服务器",
)
async def create_a2a_server(
        base_url: str = Body(..., embed=True),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[Optional[Dict]]:
    await app_config_service.create_a2a_server(
        base_url,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
        is_admin=ctx.principal.is_admin,
    )
    return Response.success()


@router.post(
    path="/a2a-servers/{a2a_id}/delete",
    response_model=Response[Optional[Dict]],
    summary="删除a2a服务器",
)
async def delete_a2a_server(
        a2a_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[Optional[Dict]]:
    await app_config_service.delete_a2a_server(
        a2a_id,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
    )
    return Response.success()


@router.post(
    path="/a2a-servers/{a2a_id}/enabled",
    response_model=Response[Optional[Dict]],
    summary="更新A2A服务的启用状态",
)
async def set_a2a_server_enabled(
        a2a_id: str,
        enabled: bool = Body(..., embed=True),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        app_config_service: AppConfigService = Depends(get_app_config_service),
) -> Response[Optional[Dict]]:
    await app_config_service.set_a2a_server_enabled(
        a2a_id,
        enabled,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
    )
    return Response.success()
