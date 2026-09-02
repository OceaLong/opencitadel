"""Retained MCP integration configuration only."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import Field

from app.contexts.inference.runtime import InferenceRuntime
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.service_dependencies import get_inference_runtime
from app.kernel.interfaces.schemas import ApiModel

router = APIRouter(prefix="/integrations/mcp", tags=["mcp"])


class MCPBody(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    transport: str = Field(default="streamable_http", pattern="^(streamable_http|stdio)$")
    config: dict[str, object] = Field(default_factory=dict)
    capability_catalog: dict[str, object] = Field(default_factory=dict)
    visibility: str = Field(default="private", pattern="^(private|global)$")


@router.get("")
async def list_mcp(runtime: InferenceRuntime = Depends(get_inference_runtime)):
    return {"data": await runtime.queries.list_mcp()}


@router.post("")
async def create_mcp(
    body: MCPBody,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: InferenceRuntime = Depends(get_inference_runtime),
):
    return {
        "data": await runtime.commands.put_mcp(
            None,
            body.model_dump(mode="json", by_alias=True),
            scope=workspace.scope,
            is_admin=workspace.principal.is_admin,
        )
    }


@router.put("/{server_id}")
async def update_mcp(
    server_id: str,
    body: MCPBody,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: InferenceRuntime = Depends(get_inference_runtime),
):
    return {
        "data": await runtime.commands.put_mcp(
            server_id,
            body.model_dump(mode="json", by_alias=True),
            scope=workspace.scope,
            is_admin=workspace.principal.is_admin,
        )
    }


@router.delete("/{server_id}")
async def delete_mcp(
    server_id: str,
    _: WorkspaceContext = Depends(get_workspace_context),
    runtime: InferenceRuntime = Depends(get_inference_runtime),
):
    await runtime.commands.delete_mcp(server_id)
    return {"data": {"deleted": True}}
