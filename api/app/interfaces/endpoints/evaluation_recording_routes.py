"""Recording jobs expose status and fixed metadata, never replay result bodies."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.application.evaluation.discovery import RecordingCandidatePage, RecordingPage
from app.application.evaluation.recording_service import RecordingService
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.evaluation.recording import RecordingJob, RecordingSelection
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.evaluation_dataset_routes import DatasetRoute, authorize
from app.interfaces.schemas.base import Response
from app.interfaces.service_dependencies import get_recording_service

router = APIRouter(
    prefix="/evaluation/recordings", tags=["Evaluation recordings"], route_class=DatasetRoute
)
logger = logging.getLogger(__name__)


class CreateRecordingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    request_id: str = Field(min_length=1, max_length=255)
    selections: tuple[RecordingSelection, ...] = Field(min_length=1, max_length=1000)


class RecordingResult(BaseModel):
    version_id: UUID
    revision: int
    slot_count: int
    tool_names: list[str]


@router.get("", response_model=Response[RecordingPage])
async def list_recordings(
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: RecordingService = Depends(get_recording_service),
):
    authorize(ctx)
    return Response(
        data=await service.list_jobs(ctx.scope, ctx.principal, cursor=cursor, limit=limit)
    )


@router.get("/sources/{run_id}", response_model=Response[RecordingCandidatePage])
async def source_candidates(
    run_id: UUID,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: RecordingService = Depends(get_recording_service),
):
    authorize(ctx, True)
    try:
        data = await service.candidates(
            ctx.scope, ctx.principal, run_id, cursor=cursor, limit=limit
        )
    except ValueError as error:
        logger.warning(
            "recording candidate read failed: %s%s",
            type(error).__name__,
            ": " + error.reason if isinstance(error, ReplayMismatch) else "",
        )
        raise
    return Response(data=data)


@router.post("", response_model=Response[RecordingJob], status_code=202)
async def create_recording(
    payload: CreateRecordingRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: RecordingService = Depends(get_recording_service),
):
    authorize(ctx, True)
    return Response(
        code=202,
        data=await service.create(
            ctx.scope,
            ctx.principal,
            payload.run_id,
            payload.selections,
            request_id=payload.request_id,
        ),
    )


@router.get("/{job_id}", response_model=Response[RecordingJob])
async def recording_status(
    job_id: UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: RecordingService = Depends(get_recording_service),
):
    authorize(ctx)
    return Response(data=await service.status(ctx.scope, ctx.principal, job_id))


@router.get("/{job_id}/result", response_model=Response[RecordingResult])
async def recording_result(
    job_id: UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: RecordingService = Depends(get_recording_service),
):
    authorize(ctx)
    return Response(data=await service.result(ctx.scope, ctx.principal, job_id))
