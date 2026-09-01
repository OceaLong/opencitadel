import json

from fastapi import APIRouter, Depends, Query
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.resource_version_shared import (
    NonAuditorWriteGuardDep,
    WorkspaceContextDep,
    to_build_response,
    to_version_response,
)
from app.interfaces.schemas import Response
from app.interfaces.schemas.knowledge_base import (
    AddKnowledgeDocumentsRequest,
    CreateKnowledgeBaseRequest,
    CreateKnowledgeBaseSessionRequest,
    CreateKnowledgeBaseSessionResponse,
    KnowledgeBaseResponse,
    KnowledgeBuildResponse,
    KnowledgeDocumentContentItemResponse,
    KnowledgeDocumentResponse,
    KnowledgeGraphResponse,
    KnowledgeVersionResponse,
    ListKnowledgeBasesResponse,
    ListKnowledgeDocumentsResponse,
    ListKnowledgeVersionsResponse,
    ReadKnowledgeDocumentResponse,
)
from app.interfaces.service_dependencies import get_knowledge_base_service

router = APIRouter(prefix="/knowledge-bases", tags=["文档知识库"])


def _to_kb_response(kb) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(**kb.model_dump(mode="json"))


def _to_doc_response(doc) -> KnowledgeDocumentResponse:
    return KnowledgeDocumentResponse(**doc.model_dump(mode="json"))


def _to_build_response(build) -> KnowledgeBuildResponse:
    return to_build_response(KnowledgeBuildResponse, build)


def _to_version_response(version) -> KnowledgeVersionResponse:
    return to_version_response(KnowledgeVersionResponse, version)


@router.post("", response_model=Response[KnowledgeBaseResponse])
async def create_knowledge_base(
    request: CreateKnowledgeBaseRequest,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[KnowledgeBaseResponse]:
    kb = await service.create_kb(name=request.name, settings=request.settings, scope=ctx.scope)
    return Response.success(data=_to_kb_response(kb))


@router.get("", response_model=Response[ListKnowledgeBasesResponse])
async def list_knowledge_bases(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[ListKnowledgeBasesResponse]:
    items = await service.list_kbs(limit=limit, offset=offset, scope=ctx.scope)
    return Response.success(
        data=ListKnowledgeBasesResponse(knowledge_bases=[_to_kb_response(item) for item in items])
    )


# 回收站列表必须在 ``GET /{kb_id}`` 之前注册，避免 "deleted" 被当作 kb_id。
@router.get("/deleted", response_model=Response[ListKnowledgeBasesResponse])
async def list_deleted_knowledge_bases(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[ListKnowledgeBasesResponse]:
    """获取回收站（已软删除）知识库列表"""
    items = await service.list_deleted_kbs(limit=limit, offset=offset, scope=ctx.scope)
    return Response.success(
        data=ListKnowledgeBasesResponse(knowledge_bases=[_to_kb_response(item) for item in items])
    )


@router.get("/{kb_id}", response_model=Response[KnowledgeBaseResponse])
async def get_knowledge_base(
    kb_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[KnowledgeBaseResponse]:
    return Response.success(data=_to_kb_response(await service.get_kb(kb_id, scope=ctx.scope)))


@router.get(
    "/{kb_id}/versions",
    response_model=Response[ListKnowledgeVersionsResponse],
)
async def list_kb_versions(
    kb_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[ListKnowledgeVersionsResponse]:
    history = await service.list_versions(kb_id, scope=ctx.scope)
    return Response.success(
        data=ListKnowledgeVersionsResponse(
            knowledge_base_id=history.knowledge_base_id,
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
    "/{kb_id}/versions/{version_id}",
    response_model=Response[KnowledgeVersionResponse],
)
async def get_kb_version(
    kb_id: str,
    version_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[KnowledgeVersionResponse]:
    version = await service.get_version(
        kb_id,
        version_id,
        scope=ctx.scope,
    )
    return Response.success(data=_to_version_response(version))


@router.post(
    "/{kb_id}/builds",
    response_model=Response[KnowledgeVersionResponse],
)
async def create_kb_build(
    kb_id: str,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[KnowledgeVersionResponse]:
    version = await service.create_build(kb_id, scope=ctx.scope)
    return Response.success(data=_to_version_response(version))


@router.post(
    "/{kb_id}/builds/{build_id}/retry",
    response_model=Response[KnowledgeVersionResponse],
)
async def retry_kb_build(
    kb_id: str,
    build_id: str,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[KnowledgeVersionResponse]:
    version = await service.retry_build(
        kb_id,
        build_id,
        scope=ctx.scope,
    )
    return Response.success(data=_to_version_response(version))


@router.post(
    "/{kb_id}/builds/{build_id}/cancel",
    response_model=Response[KnowledgeBuildResponse],
)
async def cancel_kb_build(
    kb_id: str,
    build_id: str,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[KnowledgeBuildResponse]:
    build = await service.cancel_build(
        kb_id,
        build_id,
        scope=ctx.scope,
    )
    return Response.success(data=_to_build_response(build))


@router.get(
    "/{kb_id}/versions/{version_id}/graph",
    response_model=Response[KnowledgeGraphResponse],
)
async def get_knowledge_graph(
    kb_id: str,
    version_id: str,
    q: str | None = Query(None, max_length=200),
    cursor: str | None = Query(None, min_length=1, max_length=2048),
    limit: int = Query(50, ge=1, le=100),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[KnowledgeGraphResponse]:
    graph = await service.get_version_graph(
        kb_id,
        version_id,
        q=q,
        cursor=cursor,
        limit=limit,
        scope=ctx.scope,
    )
    return Response.success(
        data=KnowledgeGraphResponse.model_validate(
            graph,
            from_attributes=True,
        )
    )


@router.delete("/{kb_id}", response_model=Response[dict | None])
async def delete_knowledge_base(
    kb_id: str,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[dict | None]:
    """软删除知识库；记录进入回收站，可恢复。"""
    await service.delete_kb(kb_id, scope=ctx.scope)
    return Response.success()


@router.post("/{kb_id}/restore", response_model=Response[dict | None])
async def restore_knowledge_base(
    kb_id: str,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[dict | None]:
    """从回收站恢复已软删除的知识库。"""
    await service.restore_kb(kb_id, scope=ctx.scope)
    return Response.success()


@router.delete("/{kb_id}/purge", response_model=Response[dict | None])
async def purge_knowledge_base(
    kb_id: str,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[dict | None]:
    """彻底清除回收站中的知识库及其级联数据（不可恢复）。"""
    await service.purge_kb(kb_id, scope=ctx.scope)
    return Response.success()


@router.post("/{kb_id}/documents", response_model=Response[KnowledgeBaseResponse])
async def add_documents(
    kb_id: str,
    request: AddKnowledgeDocumentsRequest,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[KnowledgeBaseResponse]:
    kb = await service.add_documents(
        kb_id,
        file_ids=request.file_ids,
        urls=request.urls,
        source_type=request.source_type,
        scope=ctx.scope,
    )
    return Response.success(data=_to_kb_response(kb))


@router.get("/{kb_id}/documents", response_model=Response[ListKnowledgeDocumentsResponse])
async def list_documents(
    kb_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[ListKnowledgeDocumentsResponse]:
    docs, total = await service.list_documents(kb_id, limit=limit, offset=offset, scope=ctx.scope)
    return Response.success(
        data=ListKnowledgeDocumentsResponse(
            documents=[_to_doc_response(doc) for doc in docs], total=total
        )
    )


@router.delete("/{kb_id}/documents/{doc_id}", response_model=Response[KnowledgeBaseResponse])
async def delete_document(
    kb_id: str,
    doc_id: str,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[KnowledgeBaseResponse]:
    kb = await service.delete_document(kb_id, doc_id, scope=ctx.scope)
    return Response.success(data=_to_kb_response(kb))


@router.get("/{kb_id}/ingest")
async def ingest_stream(
    kb_id: str,
    event_id: str | None = Query(None),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> EventSourceResponse:
    async def generator():
        async for event in service.stream_ingest(kb_id, latest_event_id=event_id, scope=ctx.scope):
            yield ServerSentEvent(
                id=event.cursor,
                event=event.event_type,
                data=json.dumps(event.payload, ensure_ascii=False),
            )

    return EventSourceResponse(generator())


@router.post("/{kb_id}/sessions", response_model=Response[CreateKnowledgeBaseSessionResponse])
async def create_kb_session(
    kb_id: str,
    request: CreateKnowledgeBaseSessionRequest,
    ctx: WorkspaceContextDep,
    _write_guard: NonAuditorWriteGuardDep,
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[CreateKnowledgeBaseSessionResponse]:
    session = await service.create_session_for_kb(
        kb_id,
        mode=request.mode,
        model_id=request.model_id,
        skill_id=request.skill_id,
        knowledge_base_version_id=request.knowledge_base_version_id,
        scope=ctx.scope,
    )
    return Response.success(
        data=CreateKnowledgeBaseSessionResponse(
            session_id=session.id,
            knowledge_base_id=kb_id,
            mode=session.mode,
        )
    )


@router.get(
    "/{kb_id}/versions/{version_id}/documents/{doc_id}/content",
    response_model=Response[ReadKnowledgeDocumentResponse],
)
async def read_document_version_content(
    kb_id: str,
    version_id: str,
    doc_id: str,
    page: int | None = Query(None, ge=1),
    cursor: str | None = Query(
        None,
        min_length=1,
        max_length=2048,
    ),
    limit: int = Query(30, ge=1, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[ReadKnowledgeDocumentResponse]:
    document, revision_id, source_page = await service.read_document_page(
        kb_id,
        version_id,
        doc_id,
        page=page,
        cursor=cursor,
        limit=limit,
        scope=ctx.scope,
    )
    return Response.success(
        data=ReadKnowledgeDocumentResponse(
            document=_to_doc_response(document),
            version_id=version_id,
            document_revision_id=revision_id,
            items=[
                KnowledgeDocumentContentItemResponse.model_validate(
                    item,
                    from_attributes=True,
                )
                for item in source_page.items
            ],
            next_cursor=source_page.next_cursor,
            total=source_page.total,
            truncated=source_page.truncated,
        )
    )
