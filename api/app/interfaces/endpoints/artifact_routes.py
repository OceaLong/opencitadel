"""Private Artifact query surface; public sharing was removed."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from starlette.responses import Response as BinaryResponse

from app.contexts.knowledge.runtime import KnowledgeRuntime
from app.interfaces.service_dependencies import get_knowledge_runtime

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("")
async def list_artifacts(
    run_id: UUID | None = Query(default=None, alias="runId"),
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    return {"data": await runtime.queries.list_artifacts(run_id)}


@router.get("/{artifact_id}")
async def get_artifact(
    artifact_id: UUID,
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    return {"data": await runtime.queries.get_artifact(artifact_id)}


@router.get("/{artifact_id}/content")
async def get_artifact_content(
    artifact_id: UUID,
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    content, media_type = await runtime.queries.artifact_content(artifact_id)
    return BinaryResponse(content, media_type=media_type)
