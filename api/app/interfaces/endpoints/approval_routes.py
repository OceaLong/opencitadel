"""Explicit human decisions for durable execution approvals."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.application.services.agent_service import AgentService
from app.application.services.audit_service import AuditService
from app.domain.errors import NotFoundError
from app.domain.models.audit_log import AuditLog
from app.domain.models.scope import OwnerScopeType, Principal, WorkspaceContext
from app.interfaces.auth_dependencies import (
    get_workspace_context,
    require_non_auditor,
)
from app.interfaces.client_ip import get_client_ip
from app.interfaces.schemas import Response
from app.interfaces.schemas.session import (
    DecideApprovalRequest,
    DecideApprovalResponse,
)
from app.interfaces.service_dependencies import get_agent_service, get_audit_service

router = APIRouter(prefix="/approval-batches", tags=["执行审批"])


@router.post(
    "/{approval_id}/commands/decide",
    response_model=Response[DecideApprovalResponse],
)
async def decide_approval(
    approval_id: UUID,
    body: DecideApprovalRequest,
    request: Request,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    _write_guard: Principal = Depends(require_non_auditor),
    agent_service: AgentService = Depends(get_agent_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> Response[DecideApprovalResponse]:
    try:
        run_id = await agent_service.decide_approval(
            approval_id=approval_id,
            owner_scope=ctx.scope,
            decision=body.decision,
            actor_user_id=ctx.principal.user_id,
            feedback=body.feedback,
        )
    except ValueError as error:
        raise NotFoundError(str(error)) from error
    await audit_service.record(
        AuditLog(
            actor_user_id=ctx.principal.user_id,
            actor_ip=get_client_ip(request),
            action=f"agent_approval_{body.decision}",
            resource_type="run",
            resource_id=str(run_id),
            team_id=(ctx.scope.team_id if ctx.scope.type == OwnerScopeType.TEAM else None),
            metadata={
                "approval_id": str(approval_id),
                "decision": body.decision,
                "feedback": body.feedback,
            },
        )
    )
    return Response.success(
        DecideApprovalResponse(
            run_id=run_id,
            approval_id=approval_id,
            decision=body.decision,
        )
    )
