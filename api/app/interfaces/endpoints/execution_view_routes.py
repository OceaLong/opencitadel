"""Authenticated execution reads; all persistence lives behind injected services."""

import asyncio
import json
from datetime import datetime
from functools import partial
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response as BinaryResponse
from fastapi.routing import APIRoute
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.application.dto.execution_view import (
    RunViewPage,
    StepViewPage,
    TimelineView,
    ViewPage,
)
from app.application.execution.public_projection import PublicEventPage
from app.application.ports.execution_view import (
    ViewCursorInvalid,
    ViewNotFound,
    ViewRebuilding,
    ViewRevisionExpired,
)
from app.application.services.execution_content_service import ContentPage, ExecutionContentService
from app.application.services.execution_event_service import ExecutionEventService
from app.application.services.execution_view_service import ExecutionViewService
from app.domain.errors import AppException
from app.domain.models.resource_pin import ResourceUnavailable
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.execution_view import (
    READ_RESPONSES,
    ArtifactProvenanceResponse,
    StepDetailResponse,
)
from app.interfaces.service_dependencies import (
    get_execution_content_service,
    get_execution_event_service,
    get_execution_view_service,
)

EXECUTION_ERRORS = {
    "invalid_argument": partial(AppException, error_key="executionErrors.invalid_argument"),
    "permission_denied": partial(AppException, error_key="executionErrors.permission_denied"),
    "not_found": partial(AppException, error_key="executionErrors.not_found"),
    "revision_conflict": partial(AppException, error_key="executionErrors.revision_conflict"),
    "resource_unavailable": partial(AppException, error_key="executionErrors.resource_unavailable"),
    "projection_rebuilding": partial(
        AppException, error_key="executionErrors.projection_rebuilding"
    ),
}


def read_error(status, code):
    return EXECUTION_ERRORS[code](
        code=status,
        status_code=status,
        msg=code,
        data={"code": code},
    )


class ExecutionReadRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request):
            try:
                response = await handler(request)
                response.headers["Cache-Control"] = "no-store"
                response.headers["Vary"] = "Cookie, Authorization, X-Workspace-Id"
                return response
            except (RequestValidationError, ViewCursorInvalid) as error:
                raise read_error(400, "invalid_argument") from error
            except ViewRevisionExpired as error:
                raise read_error(409, "revision_conflict") from error
            except ViewRebuilding as error:
                raise read_error(503, "projection_rebuilding") from error
            except ViewNotFound as error:
                raise read_error(404, "not_found") from error
            except ResourceUnavailable as error:
                raise read_error(409, "resource_unavailable") from error
            except PermissionError as error:
                raise read_error(403, "permission_denied") from error
            except AppException as error:
                code = {
                    400: "invalid_argument",
                    403: "permission_denied",
                    404: "not_found",
                    409: "resource_unavailable",
                    503: "projection_rebuilding",
                }.get(error.status_code)
                if code and not (isinstance(error.data, dict) and error.data.get("code")):
                    raise read_error(error.status_code, code) from error
                raise

        return wrapped


router = APIRouter(
    tags=["Execution view"], route_class=ExecutionReadRoute, responses=READ_RESPONSES
)


@router.get("/execution-runs", response_model=Response[RunViewPage])
async def list_runs(
    source_entity_type: str | None = None,
    source_entity_id: str | None = None,
    family: str | None = None,
    state: str | None = None,
    mode: str | None = None,
    configuration: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    purpose: str | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionViewService = Depends(get_execution_view_service),
):
    return Response.success(
        await service.list_runs(
            ctx.scope,
            filters={
                "source_entity_type": source_entity_type,
                "source_entity_id": source_entity_id,
                "family": family,
                "state": state,
                "mode": mode,
                "configuration": configuration,
                "start": start,
                "end": end,
                "purpose": purpose,
            },
            cursor=cursor,
            limit=limit,
        )
    )


@router.get("/execution-runs/{run_id}/view", response_model=Response[ViewPage])
async def get_view(
    run_id: UUID,
    at: str | None = None,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionViewService = Depends(get_execution_view_service),
):
    return Response.success(await service.get_view(ctx.scope, run_id, at))


@router.get("/execution-runs/{run_id}/steps", response_model=Response[StepViewPage])
async def list_steps(
    run_id: UUID,
    at: str | None = None,
    revision: int | None = Query(None, ge=0),
    parent: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    tool_name: str | None = None,
    activity_id: str | None = None,
    attempt_id: str | None = None,
    cursor: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionViewService = Depends(get_execution_view_service),
):
    return Response.success(
        await service.list_steps(
            ctx.scope,
            run_id,
            at=at,
            revision=revision,
            filters={
                "parent": parent,
                "kind": kind,
                "status": status,
                "tool_name": tool_name,
                "activity_id": activity_id,
                "attempt_id": attempt_id,
            },
            cursor=cursor,
            limit=limit,
        )
    )


@router.get("/execution-runs/{run_id}/steps/{step_id}", response_model=Response[StepDetailResponse])
async def get_step(
    run_id: UUID,
    step_id: str,
    at: str | None = None,
    attempt: str | None = None,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionViewService = Depends(get_execution_view_service),
):
    cut = await service.get_step_cut(ctx.scope, run_id, step_id, at)
    step = cut.step
    if attempt is not None and step.attempt_id != attempt:
        raise ViewNotFound("attempt not present at selected step")
    return Response.success(StepDetailResponse(**step.model_dump(), at=cut.at))


@router.get("/execution-runs/{run_id}/timeline", response_model=Response[TimelineView])
async def get_timeline(
    run_id: UUID,
    start: datetime,
    end: datetime,
    target_time: datetime | None = None,
    direction: str = "before",
    bucket_count: int = Query(100, ge=1, le=200),
    anchor_at: str | None = None,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionViewService = Depends(get_execution_view_service),
):
    return Response.success(
        await service.get_timeline(
            ctx.scope, run_id, start, end, target_time, direction, bucket_count, anchor_at
        )
    )


@router.get(
    "/execution-runs/{run_id}/steps/{step_id}/content", response_model=Response[ContentPage]
)
async def read_content(
    run_id: UUID,
    step_id: str,
    at: str,
    content_kind: str = "output",
    cursor: str | None = None,
    limit_bytes: int = Query(65536, ge=4, le=65536),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionContentService = Depends(get_execution_content_service),
):
    return Response.success(
        await service.read_step_content(
            ctx.scope, run_id, step_id, at, cursor, limit_bytes, content_kind=content_kind
        )
    )


@router.get(
    "/artifacts/{artifact_id}/provenance", response_model=Response[list[ArtifactProvenanceResponse]]
)
async def get_provenance(
    artifact_id: str,
    version: int = Query(..., ge=1),
    run_id: UUID | None = None,
    at: str | None = None,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionContentService = Depends(get_execution_content_service),
):
    rows = await service.get_public_provenance(
        ctx.scope, artifact_id, version, run_id=run_id, at=at
    )
    return Response.success([ArtifactProvenanceResponse.model_validate(row) for row in rows])


@router.get("/execution-artifacts/{artifact_id}/content", response_model=Response[ContentPage])
async def read_artifact(
    artifact_id: str,
    version: int = Query(..., ge=1),
    run_id: UUID | None = None,
    step_id: str | None = None,
    at: str | None = None,
    cursor: str | None = None,
    limit_bytes: int = Query(65536, ge=4, le=65536),
    presentation: bool = True,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionContentService = Depends(get_execution_content_service),
):
    reader = service.read_artifact_preview if presentation else service.read_artifact
    return Response.success(
        await reader(
            ctx.scope,
            artifact_id,
            version,
            run_id=run_id,
            step_id=step_id,
            at=at,
            cursor=cursor,
            limit_bytes=limit_bytes,
        )
    )


@router.get("/execution-sources/{citation_id}/content", response_model=Response[ContentPage])
async def read_source(
    citation_id: str,
    locator: Literal["citation", "page", "document"] = "citation",
    chunk_id: str | None = None,
    page_no: int | None = None,
    cursor: str | None = None,
    limit_bytes: int = Query(65536, ge=4, le=65536),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionContentService = Depends(get_execution_content_service),
):
    selection = {"citation_id": citation_id}
    if chunk_id is not None:
        selection["chunk_id"] = chunk_id
    if page_no is not None:
        selection["page_no"] = page_no
    if locator in ("page", "document"):
        selection["chunk_id"] = None
    if locator == "document":
        selection["page_no"] = None
    return Response.success(
        await service.read_source(
            ctx.scope,
            selection,
            cursor=cursor,
            limit_bytes=limit_bytes,
        )
    )


@router.get("/execution-runs/{run_id}/events", response_model=Response[PublicEventPage])
async def get_events(
    run_id: UUID,
    after: str | None = None,
    before: str | None = None,
    latest: bool = False,
    limit: int = Query(200, ge=1, le=500),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionEventService = Depends(get_execution_event_service),
):
    return Response.success(
        await service.list_events(
            ctx.scope, run_id, after=after, before=before, latest=latest, limit=limit
        )
    )


@router.get(
    "/execution-runs/{run_id}/events/stream",
    response_class=EventSourceResponse,
    responses={
        200: {
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
            "description": "Persistent public events; opaque Last-Event-ID resumes strictly after the accepted event. refresh requires reloading the view.",
        }
    },
)
async def stream_events(
    request: Request,
    run_id: UUID,
    after: str | None = None,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionEventService = Depends(get_execution_event_service),
):
    if after is not None and last_event_id is not None and after != last_event_id:
        raise ViewCursorInvalid("conflicting recovery cursors")
    cursor = after or last_event_id
    first = await service.list_events(ctx.scope, run_id, after=cursor, limit=200)

    async def stream():
        nonlocal cursor
        page = first
        while not await request.is_disconnected():
            try:
                for event in page.events:
                    # Backpressure can keep an event buffered beyond revocation.
                    await service.revalidate(ctx.scope)
                    cursor = event.cursor
                    yield ServerSentEvent(
                        id=cursor, event="execution", data=event.model_dump_json()
                    )
                await asyncio.sleep(1)
                page = await service.list_events(ctx.scope, run_id, after=cursor, limit=200)
            except (ViewNotFound, ViewRevisionExpired, ViewRebuilding, PermissionError) as error:
                code = "permission_denied" if isinstance(error, PermissionError) else error.code
                yield ServerSentEvent(event="refresh", data=json.dumps({"code": code}))
                return

    return EventSourceResponse(stream())


@router.get(
    "/execution-sources/{citation_id}/download",
    response_class=BinaryResponse,
    responses={
        200: {
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            }
        }
    },
)
async def download_file_source(
    citation_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionContentService = Depends(get_execution_content_service),
):
    data = await service.download_file_source(ctx.scope, citation_id)
    return BinaryResponse(
        data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": 'attachment; filename="source.bin"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
