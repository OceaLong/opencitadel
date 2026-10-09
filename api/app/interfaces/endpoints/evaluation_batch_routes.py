"""Scoped durable commands. These endpoints cannot dispatch tools or models."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.domain.evaluation.batch import BatchView
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.evaluation_dataset_routes import DatasetRoute, authorize
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.evaluation_batch import (
    BatchCommandRequest,
    BatchEnvironmentPage,
    ResultPage,
    StartBatchRequest,
)
from app.interfaces.schemas.execution_view import READ_RESPONSES
from app.interfaces.service_dependencies import get_batch_service

router = APIRouter(
    prefix="/evaluation/batches",
    tags=["Evaluation batches"],
    route_class=DatasetRoute,
    responses=READ_RESPONSES,
)


@router.post("", status_code=202, response_model=Response[BatchView])
async def start(
    payload: StartBatchRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_batch_service),
):
    authorize(ctx, True)
    return Response(
        code=202,
        data=await service.start(
            ctx.scope,
            ctx.principal,
            payload.request_id,
            payload.model_dump(mode="json", exclude={"request_id"}),
        ),
    )


@router.post("/{batch_id}/commands/cancel", status_code=202, response_model=Response[BatchView])
async def cancel(
    batch_id: UUID,
    payload: BatchCommandRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_batch_service),
):
    authorize(ctx, True)
    return Response(
        code=202,
        data=await service.cancel(
            ctx.scope, ctx.principal, payload.request_id, {"batch_id": str(batch_id)}
        ),
    )


@router.post(
    "/{batch_id}/commands/retry-failed", status_code=202, response_model=Response[BatchView]
)
async def retry_failed(
    batch_id: UUID,
    payload: BatchCommandRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_batch_service),
):
    authorize(ctx, True)
    return Response(
        code=202,
        data=await service.retry_failed(
            ctx.scope, ctx.principal, payload.request_id, {"batch_id": str(batch_id)}
        ),
    )


@router.get("/{batch_id}", response_model=Response[BatchView])
async def get(
    batch_id: UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_batch_service),
):
    authorize(ctx)
    return Response(data=await service.get(ctx.scope, ctx.principal, batch_id))


@router.get("/{batch_id}/results", response_model=Response[ResultPage])
async def results(
    batch_id: UUID,
    cursor: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_batch_service),
):
    authorize(ctx)
    return Response(
        data=await service.results(ctx.scope, ctx.principal, batch_id, cursor=cursor, limit=limit)
    )


@router.get("/{batch_id}/environments", response_model=Response[BatchEnvironmentPage])
async def environments(
    batch_id: UUID,
    cursor: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service=Depends(get_batch_service),
):
    authorize(ctx)
    return Response(
        data=await service.environments(
            ctx.scope, ctx.principal, batch_id, cursor=cursor, limit=limit
        )
    )
