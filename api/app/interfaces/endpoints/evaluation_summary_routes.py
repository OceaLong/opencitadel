"""Safe paged score metadata and persistent evaluation revision invalidations."""

import asyncio
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.application.evaluation.summary_service import SummaryService
from app.domain.evaluation.errors import DatasetNotFound
from app.domain.evaluation.summary import BatchListPage, SummaryPage
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.evaluation_dataset_routes import DatasetRoute, authorize
from app.interfaces.schemas.base import Response
from app.interfaces.service_dependencies import get_batch_service

router = APIRouter(
    prefix="/evaluation/batches", tags=["Evaluation summaries"], route_class=DatasetRoute
)


@router.get("", response_model=Response[BatchListPage])
async def batches(
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    batch_service=Depends(get_batch_service),
):
    authorize(ctx)
    return Response(
        data=await SummaryService(batch_service.suites).list(
            ctx.scope, ctx.principal, cursor=cursor, limit=limit
        )
    )


@router.get("/{batch_id}/summary", response_model=Response[SummaryPage])
async def summary(
    batch_id: UUID,
    source: Literal["model", "human", "rule"] = "model",
    dimension: str = Query("correctness", min_length=1, max_length=255),
    rubric_id: UUID | None = None,
    result_id: UUID | None = None,
    evaluation_revision: int | None = Query(None, ge=0),
    cursor: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    batch_service=Depends(get_batch_service),
):
    authorize(ctx)
    return Response(
        data=await SummaryService(batch_service.suites).summary(
            ctx.scope,
            ctx.principal,
            batch_id,
            source=source,
            dimension=dimension,
            rubric_id=rubric_id,
            result_id=result_id,
            evaluation_revision=evaluation_revision,
            cursor=cursor,
            limit=limit,
        )
    )


@router.get("/{batch_id}/events/stream", response_class=EventSourceResponse)
async def stream(
    request: Request,
    batch_id: UUID,
    after: str | None = None,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    batch_service=Depends(get_batch_service),
):
    authorize(ctx)
    if after is not None and last_event_id is not None and after != last_event_id:
        raise ValueError("invalid_cursor")
    service = SummaryService(batch_service.suites)
    cursor = after or last_event_id
    first = await service.events(ctx.scope, ctx.principal, batch_id, cursor=cursor)

    async def events():
        nonlocal cursor
        page = first
        while not await request.is_disconnected():
            try:
                for event in page:
                    await batch_service.get(ctx.scope, ctx.principal, batch_id)
                    cursor = event["cursor"]
                    yield ServerSentEvent(id=cursor, event="evaluation", data=json.dumps(event))
                await asyncio.sleep(1)
                page = await service.events(ctx.scope, ctx.principal, batch_id, cursor=cursor)
            except (PermissionError, DatasetNotFound):
                yield ServerSentEvent(
                    event="refresh", data=json.dumps({"code": "permission_denied"})
                )
                return

    return EventSourceResponse(events())
