from fastapi import APIRouter, Depends

from app.application.services.audit_service import AuditService
from app.application.services.service_api_key_service import ServiceApiKeyService
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_current_principal, get_workspace_context
from app.interfaces.schemas import Response
from app.interfaces.schemas.service_api_key import (
    CreatedServiceApiKeyResponse,
    CreateServiceApiKeyRequest,
    ListServiceApiKeysResponse,
    ServiceApiKeyResponse,
)
from app.interfaces.service_dependencies import (
    get_audit_service,
    get_service_api_key_service,
)
from app.interfaces.workspace_audit import record_workspace_audit

router = APIRouter(prefix="/service-keys", tags=["服务 API Key"])


@router.get("", response_model=Response[ListServiceApiKeysResponse])
async def list_service_keys(
    principal=Depends(get_current_principal),
    service: ServiceApiKeyService = Depends(get_service_api_key_service),
) -> Response[ListServiceApiKeysResponse]:
    keys = await service.list_keys(principal.user_id)
    return Response.success(
        data=ListServiceApiKeysResponse(keys=[ServiceApiKeyResponse.from_domain(k) for k in keys])
    )


@router.post("", response_model=Response[CreatedServiceApiKeyResponse])
async def create_service_key(
    request: CreateServiceApiKeyRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ServiceApiKeyService = Depends(get_service_api_key_service),
    audit: AuditService = Depends(get_audit_service),
) -> Response[CreatedServiceApiKeyResponse]:
    created = await service.create_key(user_id=ctx.principal.user_id, name=request.name)
    await record_workspace_audit(
        audit,
        ctx,
        action="service_api_key_created",
        resource_type="service_api_key",
        resource_id=created.key.id,
        metadata={"name": request.name, "prefix": created.key.prefix},
    )
    response = CreatedServiceApiKeyResponse(
        **ServiceApiKeyResponse.from_domain(created.key).model_dump(),
        plaintext=created.plaintext,
    )
    return Response.success(data=response)


@router.delete("/{key_id}", response_model=Response[dict | None])
async def revoke_service_key(
    key_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ServiceApiKeyService = Depends(get_service_api_key_service),
    audit: AuditService = Depends(get_audit_service),
) -> Response[dict | None]:
    await service.revoke_key(user_id=ctx.principal.user_id, key_id=key_id)
    await record_workspace_audit(
        audit,
        ctx,
        action="service_api_key_revoked",
        resource_type="service_api_key",
        resource_id=key_id,
    )
    return Response.success()
