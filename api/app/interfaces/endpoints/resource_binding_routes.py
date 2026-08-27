"""Owner-scoped immutable resource-version bindings."""

from fastapi import APIRouter, Depends

from app.application.services.resource_binding_service import ResourceBindingService
from app.domain.errors import BadRequestError
from app.domain.models.resource_bindings import ResourceKind
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context, require_non_auditor
from app.interfaces.schemas import Response
from app.interfaces.schemas.session import (
    ResourceBindingResponse,
    UpgradeResourceBindingRequest,
    UpgradeResourceBindingResponse,
)
from app.interfaces.service_dependencies import get_resource_binding_service

router = APIRouter(prefix="/sessions", tags=["资源版本绑定"])


def _response(binding) -> ResourceBindingResponse:
    return ResourceBindingResponse(
        binding_id=binding.id,
        resource_kind=binding.resource_kind.value,
        resource_id=binding.resource_id,
        version_id=binding.version_id,
        is_current=binding.is_current,
        supersedes_binding_id=binding.supersedes_binding_id,
    )


@router.get(
    "/{session_id}/resource-bindings",
    response_model=Response[list[ResourceBindingResponse]],
)
async def list_resource_bindings(
    session_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ResourceBindingService = Depends(get_resource_binding_service),
) -> Response[list[ResourceBindingResponse]]:
    return Response.success(
        data=[
            _response(binding) for binding in await service.current_bindings(session_id, ctx.scope)
        ]
    )


@router.post(
    "/{session_id}/resource-bindings/{resource_kind}/upgrade",
    response_model=Response[UpgradeResourceBindingResponse],
)
async def upgrade_resource_binding(
    session_id: str,
    resource_kind: str,
    request: UpgradeResourceBindingRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard=Depends(require_non_auditor),
    service: ResourceBindingService = Depends(get_resource_binding_service),
) -> Response[UpgradeResourceBindingResponse]:
    try:
        kind = ResourceKind(resource_kind)
    except ValueError as exc:
        raise BadRequestError("invalid resource kind") from exc
    old = await service.current(session_id, kind, ctx.scope)
    new = await service.upgrade(
        session_id,
        kind,
        request.target_version_id,
        actor_id=ctx.principal.user_id,
        scope=ctx.scope,
    )
    return Response.success(
        data=UpgradeResourceBindingResponse(
            old_binding_id=old.id,
            new_binding_id=new.id,
            current_version_id=new.version_id,
        )
    )


@router.get(
    "/{session_id}/resource-bindings/{resource_kind}/available-versions",
    response_model=Response[list[ResourceBindingResponse]],
)
async def list_available_resource_versions(
    session_id: str,
    resource_kind: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ResourceBindingService = Depends(get_resource_binding_service),
) -> Response[list[ResourceBindingResponse]]:
    try:
        kind = ResourceKind(resource_kind)
    except ValueError as exc:
        raise BadRequestError("invalid resource kind") from exc
    versions = await service.available_versions(session_id, kind, ctx.scope)
    return Response.success(
        data=[
            ResourceBindingResponse(
                binding_id="",
                resource_kind=version.resource_kind.value,
                resource_id=version.resource_id,
                version_id=version.version_id,
                is_current=False,
            )
            for version in versions
        ]
    )
