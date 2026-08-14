#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 本模块路由经 session_routes.py 的 .routes.extend() 聚合，勿在别处单独 include_router。
from fastapi import APIRouter, Body, Depends

from app.application.errors.exceptions import NotFoundError
from app.domain.models.scope import WorkspaceContext
from app.infrastructure.storage.postgres import get_uow
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.schemas import Response

router = APIRouter(prefix="/sessions", tags=["会话模块"])


def _approval_batch_payload(batch) -> dict:
    return batch.model_dump(mode="json")


@router.get(
    path="/{session_id}/tool-approval-batch",
    response_model=Response[dict],
    summary="获取待审批工具调用批次",
)
async def get_pending_tool_approval_batch(
        session_id: str,
        ctx: WorkspaceContext = Depends(get_workspace_context),
) -> Response[dict]:
    async with get_uow() as uow:
        session = await uow.session.get_by_id(session_id, scope=ctx.scope)
        if not session:
            raise NotFoundError("会话不存在")
        batch = (
            await uow.resource_governance.get_pending_approval_batch(
                session_id
            )
        )
        if batch is None:
            raise NotFoundError("没有待审批的工具调用批次")
        return Response.success(_approval_batch_payload(batch))


@router.patch("/{session_id}/pending-plan", response_model=Response[dict])
async def update_pending_plan(
        session_id: str,
        body: dict = Body(...),
        ctx: WorkspaceContext = Depends(get_workspace_context),
):
    async with get_uow() as uow:
        session = await uow.session.get_by_id(session_id, scope=ctx.scope)
        if not session:
            raise NotFoundError("会话不存在")
        meta = session.pending_metadata or {}
        meta["edited_plan"] = body.get("plan", body)
        await uow.session.set_pending_metadata(session_id, meta)
        await uow.commit()
    return Response.success({"updated": True})
