from typing import Any

from app.application.request_context import get_request_id
from app.application.services.audit_service import AuditService
from app.domain.models.audit_log import AuditLog
from app.domain.models.scope import OwnerScopeType, WorkspaceContext


async def record_workspace_audit(
    audit_service: AuditService,
    ctx: WorkspaceContext,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    record = getattr(audit_service, "record", None)
    if not callable(record):
        # Direct route unit tests may call the endpoint function without
        # resolving FastAPI dependencies.
        return
    await record(
        AuditLog(
            actor_user_id=ctx.principal.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            team_id=(ctx.scope.team_id if ctx.scope.type == OwnerScopeType.TEAM else None),
            request_id=get_request_id() or "",
            metadata=metadata or {},
        )
    )
