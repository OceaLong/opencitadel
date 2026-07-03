#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.application.services.artifact_service import ArtifactService
from app.domain.models.artifact import Artifact
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.schemas import Response as ApiResponse
from app.interfaces.schemas.artifact import (
    ArtifactContentResponse,
    ArtifactListResponse,
    ArtifactResponse,
    ArtifactShareResponse,
)
from app.interfaces.service_dependencies import get_artifact_service
from app.domain.models.scope import WorkspaceContext

logger = logging.getLogger(__name__)
router = APIRouter(tags=["交付物"])
share_router = APIRouter(tags=["交付物分享"])


def _to_response(artifact: Artifact) -> ArtifactResponse:
    return ArtifactResponse.model_validate(artifact.model_dump())


def _access_denied() -> HTTPException:
    return HTTPException(status_code=404, detail="交付物不存在")


@router.get("/sessions/{session_id}/artifacts", response_model=ApiResponse[ArtifactListResponse])
async def list_session_artifacts(
        session_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: ArtifactService = Depends(get_artifact_service),
):
    try:
        artifacts = await service.list_by_session(session_id, scope=ctx.scope)
    except PermissionError:
        raise HTTPException(status_code=404, detail="会话不存在")
    return ApiResponse.success(ArtifactListResponse(artifacts=[_to_response(a) for a in artifacts]))


@router.get("/artifacts/{artifact_id}", response_model=ApiResponse[ArtifactResponse])
async def get_artifact(
        artifact_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: ArtifactService = Depends(get_artifact_service),
):
    artifact = await service.get_by_id(artifact_id, scope=ctx.scope)
    if not artifact:
        raise _access_denied()
    return ApiResponse.success(_to_response(artifact))


@router.get("/artifacts/{artifact_id}/content", response_model=ApiResponse[ArtifactContentResponse])
async def get_artifact_content(
        artifact_id: str,
        version: Optional[int] = Query(None, ge=1),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: ArtifactService = Depends(get_artifact_service),
):
    artifact = await service.get_by_id(artifact_id, scope=ctx.scope)
    if not artifact:
        raise _access_denied()
    try:
        content, incomplete = await service.get_content_text(
            artifact_id, version_index=version, scope=ctx.scope,
        )
    except PermissionError:
        raise _access_denied()
    content_type = "text/markdown" if artifact.kind == "doc" else "text/html"
    return ApiResponse.success(ArtifactContentResponse(
        content=content, content_type=content_type, incomplete=incomplete,
    ))


@router.post("/artifacts/{artifact_id}/share", response_model=ApiResponse[ArtifactShareResponse])
async def share_artifact(
        artifact_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: ArtifactService = Depends(get_artifact_service),
):
    try:
        token = await service.create_share_link(artifact_id, scope=ctx.scope)
    except PermissionError:
        raise _access_denied()
    except ValueError:
        raise _access_denied()
    return ApiResponse.success(ArtifactShareResponse(
        share_token=token,
        share_url=f"/share/artifact/{token}",
    ))


@share_router.get("/share/artifact/{token}", response_model=ApiResponse[ArtifactContentResponse])
async def public_share_artifact(
        token: str,
        service: ArtifactService = Depends(get_artifact_service),
):
    artifact = await service.get_by_share_token(token)
    if not artifact:
        raise HTTPException(status_code=404, detail="分享链接无效或已过期")
    content, incomplete = await service.get_content_text(artifact.id, sanitize_html=True)
    content_type = "text/markdown" if artifact.kind == "doc" else "text/html"
    return ApiResponse.success(ArtifactContentResponse(
        content=content, content_type=content_type, incomplete=incomplete,
    ))
