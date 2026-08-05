#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 本模块路由经 session_routes.py 的 .routes.extend() 聚合，勿在别处单独 include_router。
from fastapi import APIRouter, Body, Depends

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.domain.models.scope import Principal, WorkspaceContext
from app.domain.models.tool_approval import ApprovalStatus
from app.infrastructure.storage.postgres import get_uow
from app.interfaces.auth_dependencies import get_workspace_context, require_non_auditor
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


@router.post(
    path="/{session_id}/tool-approval-batches/{batch_id}/decision",
    response_model=Response[dict],
    summary="审批工具调用批次",
)
async def decide_tool_approval_batch(
        session_id: str,
        batch_id: str,
        body: dict = Body(...),
        ctx: WorkspaceContext = Depends(get_workspace_context),
        _write_guard: Principal = Depends(require_non_auditor),
) -> Response[dict]:
    action = str(body.get("action") or "").lower()
    if action in {"approve", "approve_same"}:
        decision = ApprovalStatus.APPROVED
    elif action == "reject":
        decision = ApprovalStatus.REJECTED
    else:
        raise BadRequestError("审批动作必须是 approve 或 reject")

    async with get_uow() as uow:
        session = await uow.session.get_by_id(session_id, scope=ctx.scope)
        if not session:
            raise NotFoundError("会话不存在")
        batch = (
            await uow.resource_governance.get_pending_approval_batch(
                session_id
            )
        )
        if batch is None or batch.id != batch_id:
            raise NotFoundError("待审批工具调用批次不存在")

        requested_ids = body.get("tool_call_ids")
        has_explicit_selection = requested_ids is not None
        known_ids = {call.tool_call_id for call in batch.calls}
        pending_ids = {
            call.tool_call_id
            for call in batch.calls
            if call.status == ApprovalStatus.PENDING
        }
        has_explicit_decision = any(
            call.status != ApprovalStatus.PENDING
            and call.decided_by not in {None, "policy"}
            for call in batch.calls
        )
        selected_ids = (
            set(str(item) for item in requested_ids)
            if has_explicit_selection
            else (
                set()
                if has_explicit_decision
                else pending_ids
            )
        )
        unknown_ids = selected_ids - known_ids
        if unknown_ids:
            raise BadRequestError(
                "审批批次包含未知调用: "
                + ", ".join(sorted(unknown_ids))
            )
        if not selected_ids and has_explicit_selection:
            raise BadRequestError("审批批次至少需要一个工具调用")
        decision_ids = (
            selected_ids
            if has_explicit_selection
            else selected_ids & pending_ids
        )

        decided_calls = {}
        newly_approved_call_ids = set()
        for call in sorted(batch.calls, key=lambda item: item.ordinal):
            if call.tool_call_id not in decision_ids:
                continue
            decided = (
                await uow.resource_governance.decide_approval_call(
                    call.tool_call_id,
                    decision,
                    ctx.principal.user_id,
                )
            )
            if decided is None:
                raise NotFoundError(
                    f"工具调用[{call.tool_call_id}]不存在"
                )
            decided_calls[call.tool_call_id] = decided
            if (
                call.status == ApprovalStatus.PENDING
                and decided.status == ApprovalStatus.APPROVED
            ):
                newly_approved_call_ids.add(call.tool_call_id)

        calls = [
            decided_calls.get(call.tool_call_id, call)
            for call in batch.calls
        ]
        if any(call.status == ApprovalStatus.PENDING for call in calls):
            status = ApprovalStatus.PENDING
        elif any(call.status == ApprovalStatus.REJECTED for call in calls):
            status = ApprovalStatus.REJECTED
        elif all(
            call.status == ApprovalStatus.APPROVED for call in calls
        ):
            status = ApprovalStatus.APPROVED
        else:
            status = ApprovalStatus.PENDING
        updated = batch.model_copy(
            update={"calls": calls, "status": status}
        )
        if action == "approve_same":
            meta = dict(getattr(session, "pending_metadata", None) or {})
            approved_tools = list(meta.get("approved_tools") or [])
            for call in calls:
                if (
                    call.tool_call_id in newly_approved_call_ids
                    and call.tool_name not in approved_tools
                ):
                    approved_tools.append(call.tool_name)
            meta["approved_tools"] = approved_tools
            await uow.session.set_pending_metadata(session_id, meta)
        return Response.success(_approval_batch_payload(updated))


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
