"""User notification inbox."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.contexts.identity.runtime import IdentityRuntime
from app.domain.models.scope import Principal
from app.interfaces.auth_dependencies import get_current_principal
from app.interfaces.service_dependencies import get_identity_runtime

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    principal: Principal = Depends(get_current_principal),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    return {"data": await runtime.queries.list_notifications(principal.user_id)}


@router.post("/{notification_id}/commands/read")
async def mark_read(
    notification_id: UUID,
    principal: Principal = Depends(get_current_principal),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    return {
        "data": {
            "updated": await runtime.commands.mark_notification_read(
                notification_id,
                principal.user_id,
            )
        }
    }
