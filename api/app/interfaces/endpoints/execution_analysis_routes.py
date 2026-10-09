"""Canonical summary and scoped timezone preference resources."""

import json
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from app.application.services.execution_analysis_service import ExecutionAnalysisService
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.execution_comparison_routes import ComparisonRoute, authorize
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.execution_analysis import (
    AnalysisPreference,
    AnalysisPreferenceUpdate,
    AnalysisRunPage,
    AnalysisSummary,
)
from app.interfaces.schemas.execution_view import READ_RESPONSES
from app.interfaces.service_dependencies import (
    get_analysis_preferences,
    get_execution_analysis_service,
)

router = APIRouter(
    prefix="/execution-analysis",
    tags=["Execution analysis"],
    route_class=ComparisonRoute,
    responses=READ_RESPONSES,
)


@router.get("/summary", response_model=Response[AnalysisSummary])
async def summary(
    request: Request,
    filters: str = "{}",
    grain: Literal["hour", "day"] = "day",
    timezone: str = "UTC",
    watermark: str | None = None,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionAnalysisService = Depends(get_execution_analysis_service),
):
    authorize(ctx)
    if (
        set(request.query_params) - {"filters", "grain", "timezone", "watermark"}
        or len(filters) > 8192
        or (watermark and len(watermark) > 4096)
    ):
        raise ValueError("invalid_analysis_query")
    # Validate requested IANA zone even if a workspace preference takes priority.
    from app.domain.analysis.metrics import resolve_timezone

    resolve_timezone(None, timezone)
    parsed = json.loads(filters)
    if not isinstance(parsed, dict):
        raise ValueError("invalid_analysis_query")  # noqa: TRY004 - HTTP validation contract
    return Response(
        data=asdict(await service.summary(ctx.scope, parsed, grain, timezone, watermark))
    )


@router.get("/preferences", response_model=Response[AnalysisPreference])
async def get_preferences(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    preferences=Depends(get_analysis_preferences),
):
    authorize(ctx)
    return Response(data=await preferences.get(ctx.scope, ctx.principal))


@router.put("/preferences", response_model=Response[AnalysisPreference])
async def update_preferences(
    payload: AnalysisPreferenceUpdate,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    preferences=Depends(get_analysis_preferences),
):
    authorize(ctx)
    return Response(data=await preferences.update(ctx.scope, ctx.principal, **payload.model_dump()))


@router.get("/runs", response_model=Response[AnalysisRunPage])
async def runs(
    request: Request,
    watermark: str = Query(min_length=1, max_length=4096),
    filters: str = "{}",
    grain: Literal["hour", "day"] = "day",
    timezone: str = "UTC",
    cursor: str | None = Query(default=None, max_length=4096),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ExecutionAnalysisService = Depends(get_execution_analysis_service),
):
    authorize(ctx)
    if (
        set(request.query_params) - {"watermark", "filters", "grain", "timezone", "cursor", "limit"}
        or len(filters) > 8192
    ):
        raise ValueError("invalid_analysis_query")
    parsed = json.loads(filters)
    if not isinstance(parsed, dict):
        raise ValueError("invalid_analysis_query")  # noqa: TRY004 - HTTP validation contract
    return Response(
        data=await service.runs(
            ctx.scope, parsed, grain, timezone, watermark, cursor=cursor, limit=limit
        )
    )
