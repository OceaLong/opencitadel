"""Workspace comparison requests and fixed asynchronous artifact-difference pages."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.application.services.capability_service import execution_grants
from app.application.services.execution_comparison_service import ExecutionComparisonService
from app.application.services.execution_content_service import ContentPage
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.execution_view_routes import ExecutionReadRoute, read_error
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.execution_comparison import (
    ComparisonAlignment,
    ComparisonArtifactDiff,
    ComparisonCreate,
    ComparisonDiffPage,
    ComparisonDiffQueued,
    ComparisonEnvelope,
    ComparisonRefresh,
)
from app.interfaces.schemas.execution_view import READ_RESPONSES
from app.interfaces.service_dependencies import get_execution_comparison_service


class ComparisonRoute(ExecutionReadRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request):
            try:
                return await handler(request)
            except ValueError as error:
                reason = str(error)
                if "conflict" in reason:
                    raise read_error(409, "revision_conflict") from error
                if "not_found" in reason:
                    raise read_error(404, "not_found") from error
                if (
                    "unavailable" in reason
                    or "lease_lost" in reason
                    or "refresh_required" in reason
                ):
                    raise read_error(409, "resource_unavailable") from error
                raise read_error(400, "invalid_argument") from error

        return wrapped


router = APIRouter(
    tags=["Execution comparisons"],
    route_class=ComparisonRoute,
    responses=READ_RESPONSES,
)


def authorize(ctx):
    if "execution.read" not in execution_grants(ctx):
        raise PermissionError("comparison_permission_denied")


@router.post("/execution-comparisons", response_model=Response[ComparisonEnvelope], status_code=201)
async def create_comparison(
    payload: ComparisonCreate,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionComparisonService = Depends(get_execution_comparison_service),
):
    authorize(ctx)
    return Response(
        code=201,
        data=await service.create(
            ctx.scope, ctx.principal, payload.model_dump(mode="json", exclude_none=True)
        ),
    )


@router.post(
    "/execution-comparisons/{comparison_id}/refresh", response_model=Response[ComparisonEnvelope]
)
async def refresh_comparison(
    comparison_id: UUID,
    payload: ComparisonRefresh,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionComparisonService = Depends(get_execution_comparison_service),
):
    authorize(ctx)
    return Response(
        data=await service.refresh(
            ctx.scope,
            ctx.principal,
            str(comparison_id),
            payload.model_dump(mode="json", exclude_none=True),
        )
    )


@router.get("/execution-comparisons/{comparison_id}", response_model=Response[ComparisonEnvelope])
async def get_comparison(
    comparison_id: UUID,
    revision: int = Query(..., ge=1),
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    detail_run_ids: list[UUID] = Query(default=[], max_length=5),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionComparisonService = Depends(get_execution_comparison_service),
):
    authorize(ctx)
    return Response(
        data=await service.get(
            ctx.scope,
            ctx.principal,
            str(comparison_id),
            revision,
            cursor=cursor,
            limit=limit,
            detail_run_ids=[str(r) for r in detail_run_ids],
        )
    )


@router.post(
    "/execution-comparisons/{comparison_id}/alignments",
    response_model=Response[ComparisonEnvelope],
)
async def align_comparison(
    comparison_id: UUID,
    payload: ComparisonAlignment,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionComparisonService = Depends(get_execution_comparison_service),
):
    authorize(ctx)
    return Response(
        data=await service.align(
            ctx.scope,
            ctx.principal,
            str(comparison_id),
            payload.revision,
            payload.model_dump(mode="json", exclude_unset=True, exclude={"revision"}),
        )
    )


@router.post(
    "/execution-comparisons/{comparison_id}/artifact-diffs",
    response_model=Response[ComparisonDiffQueued],
    status_code=202,
)
async def create_artifact_diff(
    comparison_id: UUID,
    payload: ComparisonArtifactDiff,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionComparisonService = Depends(get_execution_comparison_service),
):
    authorize(ctx)
    return Response(
        data=await service.artifact_diff(
            ctx.scope,
            ctx.principal,
            str(comparison_id),
            payload.revision,
            payload.model_dump(mode="json", exclude={"revision"}),
        )
    )


@router.get("/execution-analysis/diff-jobs/{job_id}", response_model=Response[ComparisonDiffPage])
async def get_artifact_diff(
    job_id: UUID,
    cursor: str | None = None,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionComparisonService = Depends(get_execution_comparison_service),
):
    authorize(ctx)
    return Response(
        data=await service.artifact_diff_page(ctx.scope, ctx.principal, str(job_id), cursor=cursor)
    )


@router.get("/execution-comparisons/{comparison_id}/body", response_model=Response[ContentPage])
async def retained_body(
    comparison_id: UUID,
    revision: int = Query(ge=1),
    run_id: UUID = Query(),
    step_id: str = Query(min_length=1, max_length=255),
    kind: Literal["input", "output", "artifact"] = "output",
    artifact_id: str | None = Query(default=None, max_length=255),
    version: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None, max_length=32768),
    limit_bytes: int = Query(default=65536, ge=4, le=65536),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionComparisonService = Depends(get_execution_comparison_service),
):
    authorize(ctx)
    return Response(
        data=await service.bodies.read(
            ctx.scope,
            ctx.principal,
            str(comparison_id),
            revision,
            str(run_id),
            step_id,
            kind,
            artifact_id=artifact_id,
            version=version,
            cursor=cursor,
            limit_bytes=limit_bytes,
        )
    )
