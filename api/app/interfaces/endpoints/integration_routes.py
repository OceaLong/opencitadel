"""First-class Integration management API."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends

from app.application.services.integration_server_service import (
    A2AIntegrationService,
    MCPServerService,
)
from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.schemas import Response
from app.interfaces.schemas.integration import (
    A2AServerListResponse,
    A2AServerResponse,
    CreateA2AServerRequest,
    CreateMCPServerRequest,
    MCPServerListResponse,
    MCPServerResponse,
    SetIntegrationEnabledRequest,
    UpdateA2AServerRequest,
    UpdateMCPServerRequest,
)
from app.interfaces.service_dependencies import (
    get_a2a_integration_service,
    get_mcp_integration_service,
)

router = APIRouter(prefix="/integrations", tags=["Integrations"])


@router.get("/mcp-servers", response_model=Response[MCPServerListResponse])
async def list_mcp_servers(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: MCPServerService = Depends(get_mcp_integration_service),
) -> Response[MCPServerListResponse]:
    records = await service.list_servers(scope=ctx.scope)
    return Response.success(
        MCPServerListResponse(items=[MCPServerResponse.from_domain(item) for item in records])
    )


@router.post("/mcp-servers", response_model=Response[MCPServerResponse])
async def create_mcp_server(
    body: CreateMCPServerRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: MCPServerService = Depends(get_mcp_integration_service),
) -> Response[MCPServerResponse]:
    record = MCPServerRecord(id=str(uuid4()), **body.model_dump(mode="python"))
    created = await service.create_server(
        record,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
        is_admin=ctx.principal.is_admin,
    )
    return Response.success(MCPServerResponse.from_domain(created))


@router.put("/mcp-servers/{server_id}", response_model=Response[MCPServerResponse])
async def update_mcp_server(
    server_id: str,
    body: UpdateMCPServerRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: MCPServerService = Depends(get_mcp_integration_service),
) -> Response[MCPServerResponse]:
    values = body.model_dump(mode="python", exclude={"tool_policies"})
    if "tool_policies" in body.model_fields_set:
        values["tool_policies"] = body.tool_policies
    record = MCPServerRecord(id=server_id, **values)
    updated = await service.update_server(
        server_id,
        record,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
        is_admin=ctx.principal.is_admin,
    )
    return Response.success(MCPServerResponse.from_domain(updated))


@router.delete("/mcp-servers/{server_id}", response_model=Response[None])
async def delete_mcp_server(
    server_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: MCPServerService = Depends(get_mcp_integration_service),
) -> Response[None]:
    await service.delete_server(
        server_id,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
        is_admin=ctx.principal.is_admin,
    )
    return Response.success()


@router.patch(
    "/mcp-servers/{server_id}/enabled",
    response_model=Response[MCPServerResponse],
)
async def set_mcp_server_enabled(
    server_id: str,
    body: SetIntegrationEnabledRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: MCPServerService = Depends(get_mcp_integration_service),
) -> Response[MCPServerResponse]:
    updated = await service.set_enabled(
        server_id,
        body.enabled,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
        is_admin=ctx.principal.is_admin,
    )
    return Response.success(MCPServerResponse.from_domain(updated))


@router.get("/a2a-servers", response_model=Response[A2AServerListResponse])
async def list_a2a_servers(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: A2AIntegrationService = Depends(get_a2a_integration_service),
) -> Response[A2AServerListResponse]:
    records = await service.list_servers(scope=ctx.scope)
    return Response.success(
        A2AServerListResponse(items=[A2AServerResponse.from_domain(item) for item in records])
    )


@router.post("/a2a-servers", response_model=Response[A2AServerResponse])
async def create_a2a_server(
    body: CreateA2AServerRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: A2AIntegrationService = Depends(get_a2a_integration_service),
) -> Response[A2AServerResponse]:
    created = await service.create_server(
        base_url=body.base_url,
        enabled=body.enabled,
        tool_policies=body.tool_policies,
        visibility=body.visibility,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
        is_admin=ctx.principal.is_admin,
    )
    return Response.success(A2AServerResponse.from_domain(created))


@router.put("/a2a-servers/{server_id}", response_model=Response[A2AServerResponse])
async def update_a2a_server(
    server_id: str,
    body: UpdateA2AServerRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: A2AIntegrationService = Depends(get_a2a_integration_service),
) -> Response[A2AServerResponse]:
    values = body.model_dump(mode="python", exclude={"tool_policies"})
    if "tool_policies" in body.model_fields_set:
        values["tool_policies"] = body.tool_policies
    record = A2AServerRecord(id=server_id, **values)
    updated = await service.update_server(
        server_id,
        record,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
        is_admin=ctx.principal.is_admin,
    )
    return Response.success(A2AServerResponse.from_domain(updated))


@router.delete("/a2a-servers/{server_id}", response_model=Response[None])
async def delete_a2a_server(
    server_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: A2AIntegrationService = Depends(get_a2a_integration_service),
) -> Response[None]:
    await service.delete_server(
        server_id,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
        is_admin=ctx.principal.is_admin,
    )
    return Response.success()


@router.patch(
    "/a2a-servers/{server_id}/enabled",
    response_model=Response[A2AServerResponse],
)
async def set_a2a_server_enabled(
    server_id: str,
    body: SetIntegrationEnabledRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: A2AIntegrationService = Depends(get_a2a_integration_service),
) -> Response[A2AServerResponse]:
    updated = await service.set_enabled(
        server_id,
        body.enabled,
        scope=ctx.scope,
        actor_user_id=ctx.principal.user_id,
        is_admin=ctx.principal.is_admin,
    )
    return Response.success(A2AServerResponse.from_domain(updated))


__all__ = ["router"]
