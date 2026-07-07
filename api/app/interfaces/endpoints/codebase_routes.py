#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Query
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.application.services.codebase_service import CodebaseService
from app.domain.models.codebase import ArtifactKind
from app.domain.models.scope import WorkspaceContext
from app.domain.models.event_policy import should_project_event
from app.interfaces.schemas import Response
from app.interfaces.schemas.codebase import (
    ArtifactResponse,
    CodebaseResponse,
    CreateCodebaseRequest,
    CreateCodebaseSessionRequest,
    CreateCodebaseSessionResponse,
    DownloadCodebaseResponse,
    FileTreeResponse,
    ListArtifactsResponse,
    ListCodebasesResponse,
    ListSymbolsResponse,
    ReadSourceRequest,
    ReadSourceResponse,
    SymbolResponse,
)
from app.interfaces.schemas.event import EventMapper
from app.domain.external.object_storage import ObjectStoragePort
from app.interfaces.auth_dependencies import get_workspace_context, require_non_auditor
from app.interfaces.service_dependencies import get_codebase_service, get_object_storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/codebases", tags=["代码知识库"])


def _to_codebase_response(cb) -> CodebaseResponse:
    return CodebaseResponse(**cb.model_dump(mode="json"))


@router.post("", response_model=Response[CodebaseResponse])
async def create_codebase(
        request: CreateCodebaseRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        _write_guard=Depends(require_non_auditor),
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
        name: Optional[str] = None,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: CodebaseService = Depends(get_codebase_service),
) -> Response[ListSymbolsResponse]:
    symbols = await service.list_symbols_with_paths(codebase_id, name=name, scope=ctx.scope)
    return Response.success(
        data=ListSymbolsResponse(
            symbols=[
                SymbolResponse(**{**s.model_dump(mode="json"), "path": path})
                for s, path in symbols
            ]
        )
    )


@router.get("/{codebase_id}/artifacts", response_model=Response[ListArtifactsResponse])
async def list_artifacts(
        codebase_id: str,
        kind: Optional[ArtifactKind] = None,
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
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: CodebaseService = Depends(get_codebase_service),
) -> Response[ReadSourceResponse]:
    content = await service.read_source(
        codebase_id,
        request.path,
        start_line=request.start_line,
        end_line=request.end_line,
        scope=ctx.scope,
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
        event_id: Optional[str] = Query(None),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: CodebaseService = Depends(get_codebase_service),
) -> EventSourceResponse:
    async def generator():
        async for event in service.stream_ingest(codebase_id, latest_event_id=event_id, scope=ctx.scope):
            if not should_project_event(event, include_transient=True):
                continue
            sse_event = EventMapper.event_to_sse_event(event)
            yield ServerSentEvent(event=sse_event.event, data=sse_event.data.model_dump_json())

    return EventSourceResponse(generator())


@router.post("/{codebase_id}/reanalyze", response_model=Response[CodebaseResponse])
async def reanalyze(
        codebase_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: CodebaseService = Depends(get_codebase_service),
) -> Response[CodebaseResponse]:
    codebase = await service.reanalyze(codebase_id, scope=ctx.scope)
    return Response.success(data=_to_codebase_response(codebase))


@router.get("/{codebase_id}/download", response_model=Response[DownloadCodebaseResponse])
async def download_codebase(
        codebase_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: CodebaseService = Depends(get_codebase_service),
        object_storage: ObjectStoragePort = Depends(get_object_storage),
) -> Response[DownloadCodebaseResponse]:
    key = await service.package_download(codebase_id, object_storage, scope=ctx.scope)
    return Response.success(data=DownloadCodebaseResponse(snapshot_key=key))


@router.post("/{codebase_id}/sessions", response_model=Response[CreateCodebaseSessionResponse])
async def create_codebase_session(
        codebase_id: str,
        request: CreateCodebaseSessionRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: CodebaseService = Depends(get_codebase_service),
) -> Response[CreateCodebaseSessionResponse]:
    session = await service.create_session_for_codebase(
        codebase_id,
        mode=request.mode,
        model_id=request.model_id,
        skill_id=request.skill_id,
        scope=ctx.scope,
    )
    return Response.success(
        data=CreateCodebaseSessionResponse(
            session_id=session.id,
            codebase_id=codebase_id,
            mode=session.mode,
        )
    )


@router.delete("/{codebase_id}", response_model=Response[Optional[Dict]])
async def delete_codebase(
        codebase_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        _write_guard=Depends(require_non_auditor),
        service: CodebaseService = Depends(get_codebase_service),
) -> Response[Optional[Dict]]:
    await service.delete_codebase(codebase_id, scope=ctx.scope)
    return Response.success(msg="删除代码库成功")
