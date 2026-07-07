#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Query
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.domain.models.event_policy import should_project_event
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context, require_non_auditor
from app.interfaces.schemas import Response
from app.interfaces.schemas.event import EventMapper
from app.interfaces.schemas.knowledge_base import (
    AddKnowledgeDocumentsRequest,
    CreateKnowledgeBaseRequest,
    CreateKnowledgeBaseSessionRequest,
    CreateKnowledgeBaseSessionResponse,
    KnowledgeBaseResponse,
    KnowledgeDocumentResponse,
    ListKnowledgeBasesResponse,
    ListKnowledgeDocumentsResponse,
    ReadKnowledgeDocumentResponse,
)
from app.interfaces.service_dependencies import get_knowledge_base_service

router = APIRouter(prefix="/knowledge-bases", tags=["文档知识库"])


def _to_kb_response(kb) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(**kb.model_dump(mode="json"))


def _to_doc_response(doc) -> KnowledgeDocumentResponse:
    return KnowledgeDocumentResponse(**doc.model_dump(mode="json"))


@router.post("", response_model=Response[KnowledgeBaseResponse])
async def create_knowledge_base(
        request: CreateKnowledgeBaseRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        _write_guard=Depends(require_non_auditor),
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
    return Response.success(data=ListKnowledgeBasesResponse(knowledge_bases=[_to_kb_response(item) for item in items]))


@router.get("/{kb_id}", response_model=Response[KnowledgeBaseResponse])
async def get_knowledge_base(
        kb_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[KnowledgeBaseResponse]:
    return Response.success(data=_to_kb_response(await service.get_kb(kb_id, scope=ctx.scope)))


@router.delete("/{kb_id}", response_model=Response[Optional[Dict]])
async def delete_knowledge_base(
        kb_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        _write_guard=Depends(require_non_auditor),
        service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[Optional[Dict]]:
    await service.delete_kb(kb_id, scope=ctx.scope)
    return Response.success()


@router.post("/{kb_id}/documents", response_model=Response[KnowledgeBaseResponse])
async def add_documents(
        kb_id: str,
        request: AddKnowledgeDocumentsRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
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
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[ListKnowledgeDocumentsResponse]:
    docs = await service.list_documents(kb_id, scope=ctx.scope)
    return Response.success(data=ListKnowledgeDocumentsResponse(documents=[_to_doc_response(doc) for doc in docs]))


@router.delete("/{kb_id}/documents/{doc_id}", response_model=Response[KnowledgeBaseResponse])
async def delete_document(
        kb_id: str,
        doc_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        _write_guard=Depends(require_non_auditor),
        service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[KnowledgeBaseResponse]:
    kb = await service.delete_document(kb_id, doc_id, scope=ctx.scope)
    return Response.success(data=_to_kb_response(kb))


@router.get("/{kb_id}/ingest")
async def ingest_stream(
        kb_id: str,
        event_id: Optional[str] = Query(None),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> EventSourceResponse:
    async def generator():
        async for event in service.stream_ingest(kb_id, latest_event_id=event_id, scope=ctx.scope):
            if not should_project_event(event, include_transient=True):
                continue
            sse_event = EventMapper.event_to_sse_event(event)
            yield ServerSentEvent(event=sse_event.event, data=sse_event.data.model_dump_json())

    return EventSourceResponse(generator())


@router.post("/{kb_id}/reindex", response_model=Response[KnowledgeBaseResponse])
async def reindex(
        kb_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[KnowledgeBaseResponse]:
    return Response.success(data=_to_kb_response(await service.reindex(kb_id, scope=ctx.scope)))


@router.post("/{kb_id}/sessions", response_model=Response[CreateKnowledgeBaseSessionResponse])
async def create_kb_session(
        kb_id: str,
        request: CreateKnowledgeBaseSessionRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[CreateKnowledgeBaseSessionResponse]:
    session = await service.create_session_for_kb(
        kb_id,
        mode=request.mode,
        model_id=request.model_id,
        skill_id=request.skill_id,
        scope=ctx.scope,
    )
    return Response.success(data=CreateKnowledgeBaseSessionResponse(
            session_id=session.id,
            knowledge_base_id=kb_id,
            mode=session.mode,
        )
    )


@router.get("/{kb_id}/documents/{doc_id}", response_model=Response[ReadKnowledgeDocumentResponse])
async def read_document_in_kb(
        kb_id: str,
        doc_id: str,
        page: Optional[int] = Query(None, ge=1),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[ReadKnowledgeDocumentResponse]:
    doc, content = await service.read_document(doc_id, page=page, kb_id=kb_id, scope=ctx.scope)
    return Response.success(data=ReadKnowledgeDocumentResponse(document=_to_doc_response(doc), content=content))


@router.get("/documents/{doc_id}", response_model=Response[ReadKnowledgeDocumentResponse])
async def read_document(
        doc_id: str,
        page: Optional[int] = Query(None, ge=1),
        kb_id: Optional[str] = Query(None),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> Response[ReadKnowledgeDocumentResponse]:
    doc, content = await service.read_document(doc_id, page=page, kb_id=kb_id, scope=ctx.scope)
    return Response.success(data=ReadKnowledgeDocumentResponse(document=_to_doc_response(doc), content=content))
