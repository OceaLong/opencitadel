"""Owner-scoped file upload and retrieval."""

from __future__ import annotations

import urllib.parse
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from starlette.responses import Response as BinaryResponse

from app.contexts.knowledge.runtime import KnowledgeRuntime
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.service_dependencies import get_knowledge_runtime

router = APIRouter(prefix="/files", tags=["files"])


@router.get("")
async def list_files(runtime: KnowledgeRuntime = Depends(get_knowledge_runtime)):
    return {"data": await runtime.queries.list_files()}


@router.post("")
async def upload_file(
    file: UploadFile = File(...),
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    content = await file.read()
    value = await runtime.commands.upload_file(
        filename=file.filename or "upload.bin",
        mime_type=file.content_type or "application/octet-stream",
        content=content,
        scope=workspace.scope,
    )
    return {"data": value}


@router.get("/{file_id}")
async def get_file(
    file_id: UUID,
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    return {"data": await runtime.queries.get_file(file_id)}


@router.get("/{file_id}/download")
async def download_file(
    file_id: UUID,
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    content, metadata = await runtime.queries.download_file(file_id)
    filename = urllib.parse.quote(str(metadata["filename"]))
    return BinaryResponse(
        content,
        media_type=str(metadata["mimeType"]),
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{filename}"},
    )


@router.delete("/{file_id}")
async def delete_file(
    file_id: UUID,
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    await runtime.commands.delete_file(file_id)
    return {"data": {"deleted": True}}
