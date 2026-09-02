"""Cross-Run approval center backed by frozen reviewer projections."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.contexts.kernel.runtime import KernelApiRuntime
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.service_dependencies import get_kernel_api_runtime
from app.kernel.application.ports import KernelAuthorization
from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.types import OwnerScopeRef, Workflow
from app.kernel.interfaces.schemas import ApprovalDecisionRequest

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("")
async def list_approvals(
    approval_status: str | None = Query(default=None, alias="status"),
    team_id: str | None = Query(default=None, alias="teamId"),
    limit: int = Query(default=50, ge=1, le=200),
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KernelApiRuntime = Depends(get_kernel_api_runtime),
):
    return {
        "data": await runtime.queries.list_approvals(
            workspace.principal.user_id,
            status=approval_status,
            team_id=team_id,
            limit=limit,
        )
    }


@router.post("/{approval_id}/commands/decide", status_code=status.HTTP_202_ACCEPTED)
async def decide_approval(
    approval_id: UUID,
    body: ApprovalDecisionRequest,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KernelApiRuntime = Depends(get_kernel_api_runtime),
):
    context = await runtime.queries.approval_context(approval_id, workspace.principal.user_id)
    if context is None:
        raise HTTPException(status_code=404, detail={"key": "approval.notFound"})
    owner_scope = OwnerScopeRef(
        owner_user_id=context["owner_user_id"],
        team_id=context["team_id"],
    )
    command = CommandEnvelope(
        command_id=body.command_id or uuid4(),
        run_id=context["run_id"],
        workflow=Workflow(context["workflow"]),
        type="DecideApproval",
        payload={
            "approval_id": str(approval_id),
            "decision": body.decision,
            "feedback": body.feedback,
        },
        expected_stream_version=body.expected_stream_version,
        owner_scope=owner_scope,
        actor_user_id=workspace.principal.user_id,
        request_id=str(uuid4()),
        submitted_at=datetime.now(UTC),
    )
    result = await runtime.commands.submit(
        command,
        KernelAuthorization(
            actor_user_id=workspace.principal.user_id,
            allowed_scopes=(owner_scope,),
            is_admin=workspace.principal.is_admin,
        ),
    )
    return {"data": result.model_dump(mode="json", by_alias=True)}


# The incompatible v2 surface intentionally exports one router only.
inbox_router = router
