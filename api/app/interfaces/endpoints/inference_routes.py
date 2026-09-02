"""Inference endpoint, model, and binding configuration."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import Field

from app.contexts.inference.runtime import InferenceRuntime
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.service_dependencies import get_inference_runtime
from app.kernel.interfaces.schemas import ApiModel

router = APIRouter(prefix="/inference", tags=["inference"])


class EndpointBody(ApiModel):
    display_name: str = Field(min_length=1, max_length=255)
    provider: str = Field(default="openai", min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=2_000)
    credential: str = Field(default="", max_length=8_000)
    visibility: str = Field(default="private", pattern="^(private|global)$")


class ModelBody(ApiModel):
    endpoint_id: str
    display_name: str
    model_name: str
    kind: str = "chat"
    settings: dict[str, object] = Field(default_factory=dict)
    capabilities: dict[str, object] = Field(default_factory=dict)
    visibility: str = Field(default="private", pattern="^(private|global)$")


class BindingBody(ApiModel):
    model_id: str
    scope_type: str = Field(default="current", pattern="^(current|global)$")


@router.get("/endpoints")
async def list_endpoints(runtime: InferenceRuntime = Depends(get_inference_runtime)):
    return {"data": await runtime.queries.list_endpoints()}


@router.post("/endpoints")
async def create_endpoint(
    body: EndpointBody,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: InferenceRuntime = Depends(get_inference_runtime),
):
    value = await runtime.commands.put_endpoint(
        None,
        body.model_dump(mode="json", by_alias=True),
        scope=workspace.scope,
        is_admin=workspace.principal.is_admin,
    )
    return {"data": value}


@router.put("/endpoints/{endpoint_id}")
async def update_endpoint(
    endpoint_id: str,
    body: EndpointBody,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: InferenceRuntime = Depends(get_inference_runtime),
):
    value = await runtime.commands.put_endpoint(
        endpoint_id,
        body.model_dump(mode="json", by_alias=True),
        scope=workspace.scope,
        is_admin=workspace.principal.is_admin,
    )
    return {"data": value}


@router.delete("/endpoints/{endpoint_id}")
async def delete_endpoint(
    endpoint_id: str,
    _: WorkspaceContext = Depends(get_workspace_context),
    runtime: InferenceRuntime = Depends(get_inference_runtime),
):
    await runtime.commands.delete_endpoint(endpoint_id)
    return {"data": {"deleted": True}}


@router.get("/models")
async def list_models(runtime: InferenceRuntime = Depends(get_inference_runtime)):
    return {"data": await runtime.queries.list_models()}


@router.post("/models")
async def create_model(
    body: ModelBody,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: InferenceRuntime = Depends(get_inference_runtime),
):
    value = await runtime.commands.put_model(
        None,
        body.model_dump(mode="json", by_alias=True),
        scope=workspace.scope,
        is_admin=workspace.principal.is_admin,
    )
    return {"data": value}


@router.put("/models/{model_id}")
async def update_model(
    model_id: str,
    body: ModelBody,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: InferenceRuntime = Depends(get_inference_runtime),
):
    value = await runtime.commands.put_model(
        model_id,
        body.model_dump(mode="json", by_alias=True),
        scope=workspace.scope,
        is_admin=workspace.principal.is_admin,
    )
    return {"data": value}


@router.delete("/models/{model_id}")
async def delete_model(
    model_id: str,
    _: WorkspaceContext = Depends(get_workspace_context),
    runtime: InferenceRuntime = Depends(get_inference_runtime),
):
    await runtime.commands.delete_model(model_id)
    return {"data": {"deleted": True}}


@router.get("/bindings")
async def list_bindings(runtime: InferenceRuntime = Depends(get_inference_runtime)):
    return {"data": await runtime.queries.list_bindings()}


@router.put("/bindings/{purpose}")
async def set_binding(
    purpose: str,
    body: BindingBody,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: InferenceRuntime = Depends(get_inference_runtime),
):
    return {
        "data": await runtime.commands.set_binding(
            purpose,
            body.model_id,
            scope=workspace.scope,
            scope_type=body.scope_type,
            is_admin=workspace.principal.is_admin,
        )
    }
