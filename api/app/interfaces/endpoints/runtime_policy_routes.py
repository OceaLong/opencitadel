"""Global-administrator Runtime Policy API."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.application.services.runtime_policy_service import RuntimePolicyService
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context, require_admin
from app.interfaces.schemas import Response
from app.interfaces.schemas.runtime_policy import (
    ActiveExecutionPolicyResponse,
    ActiveOperationsPolicyResponse,
    CreateExecutionPolicyRevisionRequest,
    CreateOperationsPolicyRevisionRequest,
    ExecutionPolicyRevisionListResponse,
    ExecutionPolicyRevisionResponse,
    OperationsPolicyRevisionListResponse,
    OperationsPolicyRevisionResponse,
    RestorePolicyRevisionRequest,
)
from app.interfaces.service_dependencies import get_runtime_policy_service

router = APIRouter(
    prefix="/runtime-policies",
    tags=["Runtime Policy"],
    dependencies=[Depends(require_admin)],
)


@router.get("/execution", response_model=Response[ActiveExecutionPolicyResponse])
async def get_active_execution_policy(
    service: RuntimePolicyService = Depends(get_runtime_policy_service),
) -> Response[ActiveExecutionPolicyResponse]:
    active = await service.get_active_execution()
    return Response.success(data=ActiveExecutionPolicyResponse.from_domain(active))


@router.get(
    "/execution/revisions",
    response_model=Response[ExecutionPolicyRevisionListResponse],
)
async def list_execution_policy_revisions(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: RuntimePolicyService = Depends(get_runtime_policy_service),
) -> Response[ExecutionPolicyRevisionListResponse]:
    revisions = await service.list_execution_revisions(limit=limit, offset=offset)
    return Response.success(
        data=ExecutionPolicyRevisionListResponse(
            items=[ExecutionPolicyRevisionResponse.from_domain(item) for item in revisions],
            limit=limit,
            offset=offset,
        )
    )


@router.post(
    "/execution/revisions",
    response_model=Response[ActiveExecutionPolicyResponse],
)
async def create_execution_policy_revision(
    body: CreateExecutionPolicyRevisionRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: RuntimePolicyService = Depends(get_runtime_policy_service),
) -> Response[ActiveExecutionPolicyResponse]:
    active = await service.create_execution(
        policy=body.policy,
        expected_head_version=body.expected_head_version,
        expected_active_revision_id=body.expected_active_revision_id,
        note=body.note,
        actor_user_id=ctx.principal.user_id,
    )
    return Response.success(data=ActiveExecutionPolicyResponse.from_domain(active))


@router.post(
    "/execution/revisions/{revision_id}/restore",
    response_model=Response[ActiveExecutionPolicyResponse],
)
async def restore_execution_policy_revision(
    revision_id: UUID,
    body: RestorePolicyRevisionRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: RuntimePolicyService = Depends(get_runtime_policy_service),
) -> Response[ActiveExecutionPolicyResponse]:
    active = await service.restore_execution(
        revision_id=revision_id,
        expected_head_version=body.expected_head_version,
        expected_active_revision_id=body.expected_active_revision_id,
        note=body.note,
        actor_user_id=ctx.principal.user_id,
    )
    return Response.success(data=ActiveExecutionPolicyResponse.from_domain(active))


@router.get("/operations", response_model=Response[ActiveOperationsPolicyResponse])
async def get_active_operations_policy(
    service: RuntimePolicyService = Depends(get_runtime_policy_service),
) -> Response[ActiveOperationsPolicyResponse]:
    active = await service.get_active_operations()
    return Response.success(data=ActiveOperationsPolicyResponse.from_domain(active))


@router.get(
    "/operations/revisions",
    response_model=Response[OperationsPolicyRevisionListResponse],
)
async def list_operations_policy_revisions(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: RuntimePolicyService = Depends(get_runtime_policy_service),
) -> Response[OperationsPolicyRevisionListResponse]:
    revisions = await service.list_operations_revisions(limit=limit, offset=offset)
    return Response.success(
        data=OperationsPolicyRevisionListResponse(
            items=[OperationsPolicyRevisionResponse.from_domain(item) for item in revisions],
            limit=limit,
            offset=offset,
        )
    )


@router.post(
    "/operations/revisions",
    response_model=Response[ActiveOperationsPolicyResponse],
)
async def create_operations_policy_revision(
    body: CreateOperationsPolicyRevisionRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: RuntimePolicyService = Depends(get_runtime_policy_service),
) -> Response[ActiveOperationsPolicyResponse]:
    active = await service.create_operations(
        policy=body.policy,
        expected_head_version=body.expected_head_version,
        expected_active_revision_id=body.expected_active_revision_id,
        note=body.note,
        actor_user_id=ctx.principal.user_id,
    )
    return Response.success(data=ActiveOperationsPolicyResponse.from_domain(active))


@router.post(
    "/operations/revisions/{revision_id}/restore",
    response_model=Response[ActiveOperationsPolicyResponse],
)
async def restore_operations_policy_revision(
    revision_id: UUID,
    body: RestorePolicyRevisionRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: RuntimePolicyService = Depends(get_runtime_policy_service),
) -> Response[ActiveOperationsPolicyResponse]:
    active = await service.restore_operations(
        revision_id=revision_id,
        expected_head_version=body.expected_head_version,
        expected_active_revision_id=body.expected_active_revision_id,
        note=body.note,
        actor_user_id=ctx.principal.user_id,
    )
    return Response.success(data=ActiveOperationsPolicyResponse.from_domain(active))


__all__ = ["router"]
