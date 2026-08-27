from fastapi import APIRouter, Depends

from app.application.services.capability_service import CapabilityService
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.capability import CapabilityResponse
from app.interfaces.service_dependencies import get_capability_service

router = APIRouter(tags=["Capabilities"])


@router.get("/capabilities", response_model=Response[CapabilityResponse])
async def get_capabilities(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: CapabilityService = Depends(get_capability_service),
) -> Response[CapabilityResponse]:
    snapshot = await service.get_capabilities(ctx.scope)
    return Response.success(CapabilityResponse.model_validate(snapshot.model_dump()))
