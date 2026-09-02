"""Immutable knowledge bases and build Run creation."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from app.contexts.knowledge.runtime import KnowledgeRuntime
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.service_dependencies import get_knowledge_runtime
from app.kernel.interfaces.schemas import ApiModel

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge"])


class KnowledgeBody(ApiModel):
    name: str = Field(min_length=1, max_length=512)


class BuildBody(ApiModel):
    file_ids: list[UUID] = Field(min_length=1, max_length=100)


class DispositionBody(ApiModel):
    plan_hash: str = Field(min_length=64, max_length=64)
    confirmation: str = Field(min_length=1, max_length=500)


@router.get("")
async def list_knowledge(
    include_archived: bool = Query(default=False, alias="includeArchived"),
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    return {"data": await runtime.queries.list_kbs(include_archived=include_archived)}


@router.post("")
async def create_knowledge(
    body: KnowledgeBody,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    return {"data": await runtime.commands.create_kb(body.name, scope=workspace.scope)}


@router.get("/{kb_id}")
async def get_knowledge(
    kb_id: UUID,
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    return {"data": await runtime.queries.get_kb(kb_id)}


@router.get("/{kb_id}/versions")
async def list_versions(
    kb_id: UUID,
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    return {"data": await runtime.queries.list_versions(kb_id)}


@router.post("/{kb_id}/builds", status_code=202)
async def start_build(
    kb_id: UUID,
    body: BuildBody,
    workspace: WorkspaceContext = Depends(get_workspace_context),
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    return {
        "data": await runtime.commands.start_build(
            kb_id,
            body.file_ids,
            scope=workspace.scope,
            actor_user_id=workspace.principal.user_id,
        )
    }


@router.get("/{kb_id}/disposition")
async def preview_disposition(
    kb_id: UUID,
    action: str = Query(pattern="^(archive|restore|purge)$"),
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    return {"data": await runtime.dispositions.disposition(kb_id, action=action)}


async def _apply(
    kb_id: UUID,
    action: str,
    body: DispositionBody,
    runtime: KnowledgeRuntime,
):
    return {
        "data": await runtime.dispositions.apply_disposition(
            kb_id,
            action=action,
            plan_hash=body.plan_hash,
            confirmation=body.confirmation,
        )
    }


@router.post("/{kb_id}/commands/archive")
async def archive_knowledge(
    kb_id: UUID,
    body: DispositionBody,
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    return await _apply(kb_id, "archive", body, runtime)


@router.post("/{kb_id}/commands/restore")
async def restore_knowledge(
    kb_id: UUID,
    body: DispositionBody,
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    return await _apply(kb_id, "restore", body, runtime)


@router.post("/{kb_id}/commands/purge")
async def purge_knowledge(
    kb_id: UUID,
    body: DispositionBody,
    runtime: KnowledgeRuntime = Depends(get_knowledge_runtime),
):
    return await _apply(kb_id, "purge", body, runtime)
