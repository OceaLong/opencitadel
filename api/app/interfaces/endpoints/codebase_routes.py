import json
import logging

from fastapi import APIRouter, Depends, Query
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.application.services.codebase_service import CodebaseService
from app.domain.external.object_storage import ObjectStoragePort
from app.domain.models.codebase import ArtifactKind
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.resource_version_shared import (
    NonAuditorWriteGuardDep,
    WorkspaceContextDep,
    to_build_response,
    to_version_response,
)
from app.interfaces.schemas import Response
from app.interfaces.schemas.codebase import (
    ArtifactResponse,
    CodebaseBuildResponse,
    CodebaseResponse,
    CodebaseVersionResponse,
    CreateCodebaseRequest,
    CreateCodebaseSessionRequest,
    CreateCodebaseSessionResponse,
    DownloadCodebaseResponse,
    FileTreeResponse,
    ListArtifactsResponse,
    ListCodebasesResponse,
    ListCodebaseVersionsResponse,
    ListSymbolsResponse,
    ReadSourceRequest,
    ReadSourceResponse,
    SymbolResponse,
)
from app.interfaces.service_dependencies import get_codebase_service, get_object_storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/codebases", tags=["代码知识库"])


def _to_codebase_response(cb) -> CodebaseResponse:
    return CodebaseResponse(**cb.model_dump(mode="json"))


def _to_build_response(build) -> CodebaseBuildResponse:
    return to_build_response(CodebaseBuildResponse, build)


def _to_version_response(version) -> CodebaseVersionResponse:
    return to_version_response(CodebaseVersionResponse, version)


@router.post("", response_model=Response[CodebaseResponse])
async def create_codebase(
    request: CreateCodebaseRequest,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: CodebaseService = Depends(get_codebase_service),
) -> Response[CodebaseResponse]:
    codebase = await service.create_codebase(
        name=request.name,
        source_type=request.source_type,
        file_id=request.file_id,
        git_url=request.git_url,
        file_ids=request.file_ids,
        scope=ctx.scope,
    )
    return Response.success(data=_to_codebase_response(codebase))


@router.get("", response_model=Response[ListCodebasesResponse])
async def list_codebases(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: CodebaseService = Depends(get_codebase_service),
) -> Response[ListCodebasesResponse]:
    items = await service.list_codebases(limit=limit, offset=offset, scope=ctx.scope)
    return Response.success(
        data=ListCodebasesResponse(codebases=[_to_codebase_response(c) for c in items])
    )


@router.get("/{codebase_id}", response_model=Response[CodebaseResponse])
async def get_codebase(
    codebase_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: CodebaseService = Depends(get_codebase_service),
) -> Response[CodebaseResponse]:
    codebase = await service.get_codebase(codebase_id, scope=ctx.scope)
    return Response.success(data=_to_codebase_response(codebase))


@router.get(
    "/{codebase_id}/versions",
    response_model=Response[ListCodebaseVersionsResponse],
)
async def list_codebase_versions(
    codebase_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: CodebaseService = Depends(get_codebase_service),
) -> Response[ListCodebaseVersionsResponse]:
    history = await service.list_versions(codebase_id, scope=ctx.scope)
    return Response.success(
        data=ListCodebaseVersionsResponse(
            codebase_id=history.codebase_id,
            active_version_id=history.active_version_id,
            active_build=(
                _to_build_response(history.active_build)
                if history.active_build is not None
                else None
            ),
            versions=[_to_version_response(version) for version in history.versions],
        )
    )


@router.get(
    "/{codebase_id}/versions/{version_id}",
    response_model=Response[CodebaseVersionResponse],
)
async def get_codebase_version(
    codebase_id: str,
    version_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: CodebaseService = Depends(get_codebase_service),
) -> Response[CodebaseVersionResponse]:
    version = await service.get_version(
        codebase_id,
        version_id,
        scope=ctx.scope,
    )
    return Response.success(data=_to_version_response(version))


@router.post(
    "/{codebase_id}/builds",
    response_model=Response[CodebaseVersionResponse],
)
async def create_codebase_build(
    codebase_id: str,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: CodebaseService = Depends(get_codebase_service),
) -> Response[CodebaseVersionResponse]:
    version = await service.create_build(codebase_id, scope=ctx.scope)
    return Response.success(data=_to_version_response(version))


@router.post(
    "/{codebase_id}/builds/{build_id}/retry",
    response_model=Response[CodebaseVersionResponse],
)
async def retry_codebase_build(
    codebase_id: str,
    build_id: str,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: CodebaseService = Depends(get_codebase_service),
) -> Response[CodebaseVersionResponse]:
    version = await service.retry_build(
        codebase_id,
        build_id,
        scope=ctx.scope,
    )
    return Response.success(data=_to_version_response(version))


@router.post(
    "/{codebase_id}/builds/{build_id}/cancel",
    response_model=Response[CodebaseBuildResponse],
)
async def cancel_codebase_build(
    codebase_id: str,
    build_id: str,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: CodebaseService = Depends(get_codebase_service),
) -> Response[CodebaseBuildResponse]:
    build = await service.cancel_build(
        codebase_id,
        build_id,
        scope=ctx.scope,
    )
    return Response.success(data=_to_build_response(build))


@router.post(
    "/{codebase_id}/versions/{version_id}/source",
    response_model=Response[ReadSourceResponse],
)
async def read_version_source(
    codebase_id: str,
    version_id: str,
    request: ReadSourceRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: CodebaseService = Depends(get_codebase_service),
    object_storage: ObjectStoragePort = Depends(get_object_storage),
) -> Response[ReadSourceResponse]:
    content = await service.read_source(
        codebase_id,
        request.path,
        start_line=request.start_line,
        end_line=request.end_line,
        scope=ctx.scope,
        codebase_version_id=version_id,
        object_storage=object_storage,
    )
    return Response.success(
        data=ReadSourceResponse(
            path=request.path,
            content=content,
            start_line=request.start_line,
            end_line=request.end_line,
        )
    )


@router.get(
    "/{codebase_id}/versions/{version_id}/artifacts",
    response_model=Response[ListArtifactsResponse],
)
async def list_version_artifacts(
    codebase_id: str,
    version_id: str,
    kind: ArtifactKind | None = None,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: CodebaseService = Depends(get_codebase_service),
) -> Response[ListArtifactsResponse]:
    artifacts = await service.list_artifacts(
        codebase_id,
        kind=kind,
        codebase_version_id=version_id,
        scope=ctx.scope,
    )
    return Response.success(
        data=ListArtifactsResponse(
            artifacts=[
                ArtifactResponse(**artifact.model_dump(mode="json")) for artifact in artifacts
            ],
        )
    )


@router.get("/{codebase_id}/tree", response_model=Response[FileTreeResponse])
async def get_file_tree(
    codebase_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: CodebaseService = Depends(get_codebase_service),
) -> Response[FileTreeResponse]:
    tree = await service.get_file_tree(codebase_id, scope=ctx.scope)
    return Response.success(data=FileTreeResponse(tree=tree))


@router.get("/{codebase_id}/symbols", response_model=Response[ListSymbolsResponse])
async def list_symbols(
    codebase_id: str,
    name: str | None = None,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: CodebaseService = Depends(get_codebase_service),
) -> Response[ListSymbolsResponse]:
    symbols = await service.list_symbols_with_paths(codebase_id, name=name, scope=ctx.scope)
    return Response.success(
        data=ListSymbolsResponse(
            symbols=[
                SymbolResponse(**{**s.model_dump(mode="json"), "path": path}) for s, path in symbols
            ]
        )
    )


@router.get("/{codebase_id}/artifacts", response_model=Response[ListArtifactsResponse])
async def list_artifacts(
    codebase_id: str,
    kind: ArtifactKind | None = None,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: CodebaseService = Depends(get_codebase_service),
) -> Response[ListArtifactsResponse]:
    artifacts = await service.list_artifacts(codebase_id, kind=kind, scope=ctx.scope)
    return Response.success(
        data=ListArtifactsResponse(
            artifacts=[ArtifactResponse(**a.model_dump(mode="json")) for a in artifacts]
        )
    )


@router.post("/{codebase_id}/source", response_model=Response[ReadSourceResponse])
async def read_source(
    codebase_id: str,
    request: ReadSourceRequest,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: CodebaseService = Depends(get_codebase_service),
    object_storage: ObjectStoragePort = Depends(get_object_storage),
) -> Response[ReadSourceResponse]:
    content = await service.read_source(
        codebase_id,
        request.path,
        start_line=request.start_line,
        end_line=request.end_line,
        scope=ctx.scope,
        object_storage=object_storage,
    )
    return Response.success(
        data=ReadSourceResponse(
            path=request.path,
            content=content,
            start_line=request.start_line,
            end_line=request.end_line,
        )
    )


@router.get("/{codebase_id}/ingest")
async def ingest_stream(
    codebase_id: str,
    event_id: str | None = Query(None),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: CodebaseService = Depends(get_codebase_service),
) -> EventSourceResponse:
    async def generator():
        async for event in service.stream_ingest(
            codebase_id, latest_event_id=event_id, scope=ctx.scope
        ):
            yield ServerSentEvent(
                id=event.cursor,
                event=event.event_type,
                data=json.dumps(event.payload, ensure_ascii=False),
            )

    return EventSourceResponse(generator())


@router.post("/{codebase_id}/snapshots", response_model=Response[DownloadCodebaseResponse])
async def create_codebase_snapshot(
    codebase_id: str,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: CodebaseService = Depends(get_codebase_service),
    object_storage: ObjectStoragePort = Depends(get_object_storage),
) -> Response[DownloadCodebaseResponse]:
    key = await service.package_download(codebase_id, object_storage, scope=ctx.scope)
    codebase = await service.get_codebase(codebase_id, scope=ctx.scope)
    return Response.success(
        data=DownloadCodebaseResponse(
            snapshot_key=key,
            updated_at=codebase.updated_at,
        )
    )


@router.post("/{codebase_id}/sessions", response_model=Response[CreateCodebaseSessionResponse])
async def create_codebase_session(
    codebase_id: str,
    request: CreateCodebaseSessionRequest,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: CodebaseService = Depends(get_codebase_service),
) -> Response[CreateCodebaseSessionResponse]:
    session = await service.create_session_for_codebase(
        codebase_id,
        mode=request.mode,
        model_id=request.model_id,
        skill_id=request.skill_id,
        codebase_version_id=request.codebase_version_id,
        scope=ctx.scope,
    )
    return Response.success(
        data=CreateCodebaseSessionResponse(
            session_id=session.id,
            codebase_id=codebase_id,
            mode=session.mode,
        )
    )


@router.delete("/{codebase_id}", response_model=Response[dict | None])
async def delete_codebase(
    codebase_id: str,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: CodebaseService = Depends(get_codebase_service),
) -> Response[dict | None]:
    await service.delete_codebase(codebase_id, scope=ctx.scope)
    return Response.success()
