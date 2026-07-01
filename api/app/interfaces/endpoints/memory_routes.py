#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import List, Optional, Dict

from fastapi import APIRouter, Depends, Query

from app.application.services.memory_service import MemoryService
from app.domain.models.memory_entry import MemoryEntry, MemoryScope
from app.domain.models.scope import WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.memory import (
    MemoryEntryCreateRequest,
    MemoryEntryUpdateRequest,
    MemoryEntryResponse,
    MemoryEntryListResponse,
    SessionMemoryResponse,
    ClearMemoryRequest,
)
from app.interfaces.service_dependencies import get_memory_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["记忆管理"])


def _entry_response(entry: MemoryEntry) -> MemoryEntryResponse:
    return MemoryEntryResponse(**entry.model_dump())


# --- Global memory CRUD ---
memory_router = APIRouter(prefix="/memories")


@memory_router.get("", response_model=Response[MemoryEntryListResponse])
async def list_memories(
        scope: Optional[MemoryScope] = Query(default=None),
        session_id: Optional[str] = Query(default=None),
        q: Optional[str] = Query(default=None),
        tags: Optional[str] = Query(default=None),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        memory_service: MemoryService = Depends(get_memory_service),
) -> Response[MemoryEntryListResponse]:
    tag_list = tags.split(",") if tags else None
    entries = await memory_service.list_entries(
        scope=scope,
        session_id=session_id,
        q=q,
        tags=tag_list,
        owner_scope=ctx.scope,
    )
    return Response.success(
        data=MemoryEntryListResponse(entries=[_entry_response(e) for e in entries])
    )


@memory_router.post("", response_model=Response[MemoryEntryResponse])
async def create_memory(
        request: MemoryEntryCreateRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        memory_service: MemoryService = Depends(get_memory_service),
) -> Response[MemoryEntryResponse]:
    entry = MemoryEntry(**request.model_dump())
    created = await memory_service.create_entry(entry, owner_scope=ctx.scope)
    return Response.success(msg="创建记忆成功", data=_entry_response(created))


@memory_router.get("/{entry_id}", response_model=Response[MemoryEntryResponse])
async def get_memory(
        entry_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        memory_service: MemoryService = Depends(get_memory_service),
) -> Response[MemoryEntryResponse]:
    entry = await memory_service.get_entry(entry_id, owner_scope=ctx.scope)
    return Response.success(data=_entry_response(entry))


@memory_router.put("/{entry_id}", response_model=Response[MemoryEntryResponse])
async def update_memory(
        entry_id: str,
        request: MemoryEntryUpdateRequest,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        memory_service: MemoryService = Depends(get_memory_service),
) -> Response[MemoryEntryResponse]:
    existing = await memory_service.get_entry(entry_id, owner_scope=ctx.scope)
    data = existing.model_dump()
    for k, v in request.model_dump(exclude_unset=True).items():
        if v is not None:
            data[k] = v
    updated = MemoryEntry(**data)
    result = await memory_service.update_entry(entry_id, updated, owner_scope=ctx.scope)
    return Response.success(msg="更新记忆成功", data=_entry_response(result))


@memory_router.delete("/{entry_id}", response_model=Response[Optional[Dict]])
async def delete_memory(
        entry_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
        memory_service: MemoryService = Depends(get_memory_service),
) -> Response[Optional[Dict]]:
    await memory_service.delete_entry(entry_id, owner_scope=ctx.scope)
    return Response.success(msg="删除记忆成功")
