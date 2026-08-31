from fastapi import APIRouter, Depends
from fastapi import Response as HttpResponse

from app.application.services.audit_service import AuditService
from app.application.services.inference_binding_service import InferenceBindingService
from app.application.services.inference_endpoint_service import InferenceEndpointService
from app.application.services.inference_model_service import InferenceModelService
from app.application.services.inference_status_service import InferenceStatusService
from app.domain.errors import ForbiddenError
from app.domain.models.inference import (
    InferenceEndpoint,
    InferenceModel,
    InferencePurpose,
    ResourceVisibility,
)
from app.domain.models.scope import OwnerScopeType, Principal, WorkspaceContext
from app.domain.models.team import TeamRole
from app.interfaces.auth_dependencies import get_workspace_context, require_non_auditor
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.inference import (
    InferenceBindingListResponse,
    InferenceBindingRequest,
    InferenceBindingResponse,
    InferenceBindingScope,
    InferenceEndpointListResponse,
    InferenceEndpointResponse,
    InferenceEndpointUpsertRequest,
    InferenceModelListResponse,
    InferenceModelResponse,
    InferenceModelUpsertRequest,
    InferenceProbeResponse,
    InferenceStatusResponse,
)
from app.interfaces.service_dependencies import (
    get_audit_service,
    get_inference_binding_service,
    get_inference_endpoint_service,
    get_inference_model_service,
    get_inference_status_service,
)
from app.interfaces.workspace_audit import record_workspace_audit

router = APIRouter(prefix="/inference", tags=["Inference"])


def _force_private_for_non_admin(resource, ctx: WorkspaceContext) -> None:
    if ctx.principal.is_admin:
        return
    resource.visibility = ResourceVisibility.PRIVATE
    resource.owner_user_id = ctx.principal.user_id


def _binding_target_scope(
    binding_scope: InferenceBindingScope,
    ctx: WorkspaceContext,
):
    if binding_scope is InferenceBindingScope.GLOBAL:
        if not ctx.principal.is_admin:
            raise ForbiddenError("只有管理员可修改全局推理绑定")
        return None
    if ctx.scope.type is OwnerScopeType.TEAM:
        role = ctx.principal.team_roles.get(ctx.scope.team_id or "")
        if role not in {TeamRole.OWNER, TeamRole.ADMIN}:
            raise ForbiddenError("只有团队所有者或管理员可修改团队推理绑定")
    return ctx.scope


@router.get("/endpoints", response_model=Response[InferenceEndpointListResponse])
async def list_endpoints(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: InferenceEndpointService = Depends(get_inference_endpoint_service),
) -> Response[InferenceEndpointListResponse]:
    endpoints = await service.list_endpoints(scope=ctx.scope)
    return Response.success(
        InferenceEndpointListResponse(
            items=[InferenceEndpointResponse.from_domain(item) for item in endpoints]
        )
    )


@router.get("/endpoints/{endpoint_id}", response_model=Response[InferenceEndpointResponse])
async def get_endpoint(
    endpoint_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: InferenceEndpointService = Depends(get_inference_endpoint_service),
) -> Response[InferenceEndpointResponse]:
    return Response.success(
        InferenceEndpointResponse.from_domain(
            await service.get_endpoint(endpoint_id, scope=ctx.scope)
        )
    )


@router.post("/endpoints", response_model=Response[InferenceEndpointResponse])
async def create_endpoint(
    request: InferenceEndpointUpsertRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: InferenceEndpointService = Depends(get_inference_endpoint_service),
    audit: AuditService = Depends(get_audit_service),
) -> Response[InferenceEndpointResponse]:
    endpoint = InferenceEndpoint(**request.model_dump())
    _force_private_for_non_admin(endpoint, ctx)
    created = await service.create_endpoint(
        endpoint,
        scope=ctx.scope,
        allow_global_mutation=ctx.principal.is_admin,
    )
    await record_workspace_audit(
        audit,
        ctx,
        action="inference_endpoint_create",
        resource_type="inference_endpoint",
        resource_id=created.id,
        metadata={"credential_changed": bool(request.credential)},
    )
    return Response.success(InferenceEndpointResponse.from_domain(created))


@router.put("/endpoints/{endpoint_id}", response_model=Response[InferenceEndpointResponse])
async def update_endpoint(
    endpoint_id: str,
    request: InferenceEndpointUpsertRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: InferenceEndpointService = Depends(get_inference_endpoint_service),
    audit: AuditService = Depends(get_audit_service),
) -> Response[InferenceEndpointResponse]:
    existing = await service.get_endpoint(
        endpoint_id,
        scope=ctx.scope,
        include_credential=True,
    )
    if existing.visibility is ResourceVisibility.GLOBAL and not ctx.principal.is_admin:
        raise ForbiddenError("全局推理端点仅管理员可修改")
    updates = InferenceEndpoint(
        **request.model_dump(),
        id=endpoint_id,
        owner_user_id=existing.owner_user_id,
        team_id=existing.team_id,
        created_at=existing.created_at,
    )
    _force_private_for_non_admin(updates, ctx)
    updated = await service.update_endpoint(
        endpoint_id,
        updates,
        scope=ctx.scope,
        allow_global_mutation=ctx.principal.is_admin,
    )
    await record_workspace_audit(
        audit,
        ctx,
        action="inference_endpoint_update",
        resource_type="inference_endpoint",
        resource_id=endpoint_id,
        metadata={"credential_changed": bool(request.credential)},
    )
    return Response.success(InferenceEndpointResponse.from_domain(updated))


@router.delete("/endpoints/{endpoint_id}", response_model=Response[None])
async def delete_endpoint(
    endpoint_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: InferenceEndpointService = Depends(get_inference_endpoint_service),
) -> Response[None]:
    await service.delete_endpoint(
        endpoint_id,
        scope=ctx.scope,
        allow_global_mutation=ctx.principal.is_admin,
    )
    return Response.success()


@router.get("/models", response_model=Response[InferenceModelListResponse])
async def list_models(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: InferenceModelService = Depends(get_inference_model_service),
) -> Response[InferenceModelListResponse]:
    models = await service.list_models(scope=ctx.scope)
    return Response.success(
        InferenceModelListResponse(
            items=[InferenceModelResponse.from_domain(item) for item in models]
        )
    )


@router.get("/models/{model_id}", response_model=Response[InferenceModelResponse])
async def get_model(
    model_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: InferenceModelService = Depends(get_inference_model_service),
) -> Response[InferenceModelResponse]:
    return Response.success(
        InferenceModelResponse.from_domain(await service.get_model(model_id, scope=ctx.scope))
    )


@router.post("/models", response_model=Response[InferenceModelResponse])
async def create_model(
    request: InferenceModelUpsertRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: InferenceModelService = Depends(get_inference_model_service),
) -> Response[InferenceModelResponse]:
    model = InferenceModel(**request.model_dump())
    _force_private_for_non_admin(model, ctx)
    created = await service.create_model(
        model,
        scope=ctx.scope,
        allow_global_mutation=ctx.principal.is_admin,
    )
    return Response.success(InferenceModelResponse.from_domain(created))


@router.put("/models/{model_id}", response_model=Response[InferenceModelResponse])
async def update_model(
    model_id: str,
    request: InferenceModelUpsertRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: InferenceModelService = Depends(get_inference_model_service),
) -> Response[InferenceModelResponse]:
    existing = await service.get_model(model_id, scope=ctx.scope)
    if existing.visibility is ResourceVisibility.GLOBAL and not ctx.principal.is_admin:
        raise ForbiddenError("全局推理模型仅管理员可修改")
    updates = InferenceModel(
        **request.model_dump(),
        id=model_id,
        owner_user_id=existing.owner_user_id,
        team_id=existing.team_id,
        created_at=existing.created_at,
    )
    _force_private_for_non_admin(updates, ctx)
    updated = await service.update_model(
        model_id,
        updates,
        scope=ctx.scope,
        allow_global_mutation=ctx.principal.is_admin,
    )
    return Response.success(InferenceModelResponse.from_domain(updated))


@router.delete("/models/{model_id}", response_model=Response[None])
async def delete_model(
    model_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: InferenceModelService = Depends(get_inference_model_service),
) -> Response[None]:
    await service.delete_model(
        model_id,
        scope=ctx.scope,
        allow_global_mutation=ctx.principal.is_admin,
    )
    return Response.success()


@router.post(
    "/models/{model_id}/probe",
    response_model=Response[InferenceProbeResponse],
)
async def probe_model(
    model_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard: Principal = Depends(require_non_auditor),
    service: InferenceModelService = Depends(get_inference_model_service),
    audit: AuditService = Depends(get_audit_service),
) -> Response[InferenceProbeResponse]:
    result = await service.probe_model(model_id, scope=ctx.scope)
    await record_workspace_audit(
        audit,
        ctx,
        action="inference_model_probe",
        resource_type="inference_model",
        resource_id=model_id,
        metadata={"decision": result.status.value, "error_key": result.error_key},
    )
    return Response.success(InferenceProbeResponse.from_domain(result))


@router.get("/bindings", response_model=Response[InferenceBindingListResponse])
async def list_bindings(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: InferenceBindingService = Depends(get_inference_binding_service),
) -> Response[InferenceBindingListResponse]:
    bindings = await service.list_bindings(scope=ctx.scope)
    return Response.success(
        InferenceBindingListResponse(
            items=[InferenceBindingResponse.from_domain(item) for item in bindings]
        )
    )


@router.put("/bindings/{purpose}", response_model=Response[InferenceBindingResponse])
async def set_binding(
    purpose: InferencePurpose,
    request: InferenceBindingRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard: Principal = Depends(require_non_auditor),
    service: InferenceBindingService = Depends(get_inference_binding_service),
) -> Response[InferenceBindingResponse]:
    scope = _binding_target_scope(request.binding_scope, ctx)
    binding = await service.set_binding(purpose, request.model_id, scope=scope)
    return Response.success(InferenceBindingResponse.from_domain(binding))


@router.delete("/bindings/{purpose}", response_model=Response[None])
async def delete_binding(
    purpose: InferencePurpose,
    binding_scope: InferenceBindingScope = InferenceBindingScope.WORKSPACE,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard: Principal = Depends(require_non_auditor),
    service: InferenceBindingService = Depends(get_inference_binding_service),
) -> Response[None]:
    scope = _binding_target_scope(binding_scope, ctx)
    await service.delete_binding(purpose, scope=scope)
    return Response.success()


@router.get("/status", response_model=Response[InferenceStatusResponse])
async def get_status(
    response: HttpResponse,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: InferenceStatusService = Depends(get_inference_status_service),
) -> Response[InferenceStatusResponse]:
    response.headers["Cache-Control"] = "no-store"
    return Response.success(
        InferenceStatusResponse.model_validate(await service.get_status(ctx.scope))
    )
