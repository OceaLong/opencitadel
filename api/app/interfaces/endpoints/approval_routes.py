"""Explicit human decisions for durable execution approvals."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.application.services.agent_service import AgentService
from app.application.services.approval_inbox_service import ApprovalInboxService
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
from app.interfaces.service_dependencies import (
    get_agent_service,
    get_approval_inbox_service,
    get_audit_service,
)

router = APIRouter(prefix="/approval-batches", tags=["执行审批"])
inbox_router = APIRouter(prefix="/approvals", tags=["执行审批"])

ApprovalStatus = Literal["pending", "approved", "rejected", "cancelled", "expired"]


class ApprovalInboxItem(BaseModel):
    approval_id: UUID
    run_id: UUID
    source_entity_type: str
    source_entity_id: str
    approval_kind: str
    subject_activity_id: UUID
    subject_label: str
    risk_summary: str
    status: str
    decision: str | None
    decided_by_user_id: str | None
    requested_at: datetime
    decided_at: datetime | None


class ApprovalInboxResponse(BaseModel):
    items: list[ApprovalInboxItem]
    limit: int
    offset: int


@inbox_router.get("", response_model=Response[ApprovalInboxResponse])
async def list_approvals(
    status: ApprovalStatus | None = Query(
        default=None,
        description="按审批状态过滤；缺省返回全部状态",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    inbox_service: ApprovalInboxService = Depends(get_approval_inbox_service),
) -> Response[ApprovalInboxResponse]:
    entries = await inbox_service.list_approvals(
        owner_scope=ctx.scope,
        status=status,
        limit=limit,
        offset=offset,
    )
    return Response.success(
        ApprovalInboxResponse(
            items=[
                ApprovalInboxItem(
                    approval_id=entry.approval_id,
                    run_id=entry.run_id,
                    source_entity_type=entry.source_entity_type,
                    source_entity_id=entry.source_entity_id,
                    approval_kind=entry.approval_kind,
                    subject_activity_id=entry.subject_activity_id,
                    subject_label=entry.subject_label,
                    risk_summary=entry.risk_summary,
                    status=entry.status,
                    decision=entry.decision,
                    decided_by_user_id=entry.decided_by_user_id,
                    requested_at=entry.requested_at,
                    decided_at=entry.decided_at,
                )
                for entry in entries
            ],
            limit=limit,
            offset=offset,
        )
    )


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
