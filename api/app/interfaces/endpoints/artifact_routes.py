import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.application.security.authorization_context import authorization_scope
from app.application.services.artifact_service import ArtifactService
from app.domain.errors import NotFoundError
from app.domain.models.artifact import Artifact
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.schemas import Response as ApiResponse
from app.interfaces.schemas.artifact import (
    ArtifactContentResponse,
    ArtifactListResponse,
    ArtifactResponse,
    ArtifactShareResponse,
)
from app.interfaces.service_dependencies import get_artifact_service, get_execution_content_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["交付物"])
share_router = APIRouter(tags=["交付物分享"])


def _to_response(artifact: Artifact) -> ArtifactResponse:
    data = artifact.model_dump()
    expires = artifact.share_expires_at
    is_active = bool(artifact.share_token) and (expires is None or expires > datetime.now(UTC))
    # model_dump 中的 share_token 未在 ArtifactResponse 声明,pydantic 会忽略,
    # 从而保证完整令牌不外泄;此处只回传脱敏后的分享状态。
    data["is_shared"] = is_active
    data["share_expires_at"] = expires
    data["share_token_preview"] = (
        artifact.share_token[-4:] if is_active and artifact.share_token else None
    )
    return ArtifactResponse.model_validate(data)


def _access_denied() -> NotFoundError:
    return NotFoundError("交付物不存在", error_key="apiErrors.artifact.notFound")


@router.get("/sessions/{session_id}/artifacts", response_model=ApiResponse[ArtifactListResponse])
async def list_session_artifacts(
    session_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ArtifactService = Depends(get_artifact_service),
):
    try:
        artifacts = await service.list_by_session(session_id, scope=ctx.scope)
    except PermissionError as exc:
        raise NotFoundError("会话不存在", error_key="apiErrors.artifact.sessionNotFound") from exc
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
    version: int | None = Query(None, ge=1),
    cursor: str | None = None,
    limit_bytes: int | None = Query(None, ge=4, le=65536),
    run_id: UUID | None = None,
    step_id: str | None = None,
    at: str | None = None,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ArtifactService = Depends(get_artifact_service),
    content_service=Depends(get_execution_content_service),
):
    artifact = await service.get_by_id(artifact_id, scope=ctx.scope)
    if not artifact:
        raise _access_denied()
    from app.application.ports.execution_view import ViewCursorInvalid, ViewNotFound
    from app.domain.errors import BadRequestError
    from app.domain.models.resource_pin import ResourceUnavailable

    if (at is not None or run_id is not None or step_id is not None) and version is None:
        raise BadRequestError("historical artifact selection requires an explicit version")
    selected = version if version is not None else len(artifact.version_refs)
    try:
        page = await content_service.read_artifact_preview(
            ctx.scope,
            artifact_id,
            selected,
            cursor=cursor,
            limit_bytes=limit_bytes if limit_bytes is not None else 65536,
            complete=limit_bytes is None
            and cursor is None
            and run_id is None
            and step_id is None
            and at is None,
            run_id=run_id,
            step_id=step_id,
            at=at,
        )
    except ViewCursorInvalid as exc:
        raise BadRequestError(str(exc)) from exc
    except (PermissionError, ViewNotFound, ResourceUnavailable) as exc:
        raise _access_denied() from exc
    return ApiResponse.success(
        ArtifactContentResponse(
            content=page.content or "",
            content_type="text/markdown" if artifact.kind == "doc" else "text/html",
            artifact_id=artifact_id,
            version=selected,
            truncated=page.truncated,
            incomplete=page.truncated,
            next_cursor=page.next_cursor,
            at=page.at,
        )
    )


@router.post("/artifacts/{artifact_id}/share", response_model=ApiResponse[ArtifactShareResponse])
async def share_artifact(
    artifact_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ArtifactService = Depends(get_artifact_service),
):
    try:
        token, expires_at = await service.create_share_link(artifact_id, scope=ctx.scope)
    except PermissionError as exc:
        raise _access_denied() from exc
    except ValueError as exc:
        raise _access_denied() from exc
    return ApiResponse.success(
        ArtifactShareResponse(
            share_token=token,
            share_url=f"/share/artifact/{token}",
            share_expires_at=expires_at,
        )
    )


@router.delete("/artifacts/{artifact_id}/share", response_model=ApiResponse[dict])
async def revoke_artifact_share(
    artifact_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: ArtifactService = Depends(get_artifact_service),
):
    try:
        await service.revoke_share_link(artifact_id, scope=ctx.scope)
    except PermissionError as exc:
        raise _access_denied() from exc
    except ValueError as exc:
        raise _access_denied() from exc
    return ApiResponse.success({"revoked": True})


@share_router.get("/share/artifact/{token}", response_model=ApiResponse[ArtifactContentResponse])
async def public_share_artifact(
    token: str,
    service: ArtifactService = Depends(get_artifact_service),
):
    with authorization_scope(AuthorizationContext.system("public-artifact-share")):
        artifact = await service.get_by_share_token(token)
        if not artifact:
            raise NotFoundError("分享链接无效或已过期", error_key="apiErrors.artifact.shareInvalid")
        content, incomplete = await service.get_content_text(artifact.id, sanitize_html=True)
    content_type = "text/markdown" if artifact.kind == "doc" else "text/html"
    return ApiResponse.success(
        ArtifactContentResponse(
            content=content,
            content_type=content_type,
            incomplete=incomplete,
        )
    )
