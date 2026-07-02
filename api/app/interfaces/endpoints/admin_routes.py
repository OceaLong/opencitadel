#!/usr/bin/env python
# -*- coding: utf-8 -*-
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from starlette.responses import StreamingResponse

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.application.services.audit_service import AuditService
from app.application.services.usage_stats_service import UsageStatsService
from app.domain.models.invitation import Invitation, InvitationType
from app.domain.models.user import UserStatus
from app.domain.models.user_quota import UserQuota
from app.interfaces.auth_dependencies import require_admin
from app.interfaces.schemas import Response
from app.interfaces.schemas.admin import (
    AuditLogResponse,
    CreatePlatformInvitationRequest,
    ListAdminUsersResponse,
    ListAuditLogsResponse,
    PatchUserRequest,
    QuotaRequest,
)
from app.interfaces.schemas.team import InvitationLinkResponse
from app.interfaces.schemas.admin import AdminUserResponse
from app.interfaces.service_dependencies import get_audit_service, get_usage_stats_service
from app.infrastructure.storage.postgres import get_uow
from core.config import get_settings

router = APIRouter(prefix="/admin", tags=["管理员"])

_OWNED_RESOURCE_TABLES = (
    "sessions",
    "memory_entries",
    "knowledge_bases",
    "codebases",
    "files",
    "llm_token_usages",
)


async def _transfer_user_resources_to_team(db_session, user_id: str, team_id: str) -> None:
    for table in _OWNED_RESOURCE_TABLES:
        await db_session.execute(
            text(f"UPDATE {table} SET owner_user_id = NULL, team_id = :team_id WHERE owner_user_id = :user_id"),
            {"team_id": team_id, "user_id": user_id},
        )


async def _delete_user_owned_resources(db_session, user_id: str) -> None:
    for table in _OWNED_RESOURCE_TABLES:
        await db_session.execute(
            text(f"DELETE FROM {table} WHERE owner_user_id = :user_id"),
            {"user_id": user_id},
        )


async def _revoke_user_security_material(db_session, user_id: str) -> None:
    await db_session.execute(
        text("UPDATE service_api_keys SET revoked_at = CURRENT_TIMESTAMP WHERE owner_user_id = :user_id AND revoked_at IS NULL"),
        {"user_id": user_id},
    )
    await db_session.execute(text("DELETE FROM oauth_identities WHERE user_id = :user_id"), {"user_id": user_id})
    await db_session.execute(text("DELETE FROM team_members WHERE user_id = :user_id"), {"user_id": user_id})


@router.get("/users", response_model=Response[ListAdminUsersResponse], dependencies=[Depends(require_admin)])
async def list_users(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
) -> Response[ListAdminUsersResponse]:
    async with get_uow() as uow:
        users = await uow.user.list(limit=limit, offset=offset)
    return Response.success(data=ListAdminUsersResponse(users=[AdminUserResponse.from_domain(u) for u in users]))


@router.patch("/users/{user_id}", response_model=Response[AdminUserResponse], dependencies=[Depends(require_admin)])
async def patch_user(user_id: str, request: PatchUserRequest) -> Response[AdminUserResponse]:
    async with get_uow() as uow:
        user = await uow.user.get_by_id(user_id)
        if not user:
            raise NotFoundError("用户不存在")
        if request.global_role is not None:
            user.global_role = request.global_role
        if request.status is not None:
            user.status = request.status
            user.token_version += 1
            await uow.refresh_token.revoke_all_for_user(user.id)
        if request.display_name is not None:
            user.display_name = request.display_name
        user.updated_at = datetime.now()
        await uow.user.save(user)
    return Response.success(data=AdminUserResponse.from_domain(user))


@router.delete("/users/{user_id}", response_model=Response[dict], dependencies=[Depends(require_admin)])
async def delete_user(
        user_id: str,
        strategy: str = Query("anonymize", pattern="^(cascade|transfer_to_team|anonymize)$"),
        target_team_id: Optional[str] = Query(None),
) -> Response[dict]:
    if strategy == "transfer_to_team" and not target_team_id:
        raise BadRequestError("转移策略需要 target_team_id")
    async with get_uow() as uow:
        user = await uow.user.get_by_id(user_id)
        if not user:
            raise NotFoundError("用户不存在")
        if strategy == "cascade":
            await _delete_user_owned_resources(uow.db_session, user_id)
            await uow.user.delete_by_id(user_id)
        else:
            user.status = UserStatus.DISABLED
            user.token_version += 1
            if strategy == "anonymize":
                user.email = f"deleted-{user.id}@deleted.local"
                user.username = f"deleted-{user.id}"
                user.display_name = "Deleted User"
            elif strategy == "transfer_to_team":
                if not await uow.team.get_by_id(target_team_id):
                    raise NotFoundError("目标团队不存在")
                await _transfer_user_resources_to_team(uow.db_session, user_id, target_team_id)
            await uow.user.save(user)
            await uow.refresh_token.revoke_all_for_user(user.id)
            await _revoke_user_security_material(uow.db_session, user.id)
    return Response.success(msg="用户删除策略已执行", data={"strategy": strategy})


@router.post("/invitations", response_model=Response[InvitationLinkResponse], dependencies=[Depends(require_admin)])
async def create_platform_invitation(request: CreatePlatformInvitationRequest) -> Response[InvitationLinkResponse]:
    token = secrets.token_urlsafe(32)
    invitation = Invitation(
        type=InvitationType.PLATFORM,
        email=request.email.strip().lower(),
        token=token,
        expires_at=datetime.now() + timedelta(days=7),
    )
    async with get_uow() as uow:
        await uow.invitation.save(invitation)
    url = f"{get_settings().frontend_base_url.rstrip('/')}/register?invite_token={token}"
    return Response.success(data=InvitationLinkResponse(url=url), msg="平台邀请链接已生成")


@router.get("/users/{user_id}/quota", response_model=Response[QuotaRequest], dependencies=[Depends(require_admin)])
async def get_quota(user_id: str) -> Response[QuotaRequest]:
    async with get_uow() as uow:
        quota = await uow.quota.get_for_user(user_id)
    return Response.success(data=QuotaRequest(**quota.model_dump()) if quota else QuotaRequest())


@router.put("/users/{user_id}/quota", response_model=Response[QuotaRequest], dependencies=[Depends(require_admin)])
async def put_quota(user_id: str, request: QuotaRequest) -> Response[QuotaRequest]:
    quota = UserQuota(user_id=user_id, **request.model_dump())
    async with get_uow() as uow:
        await uow.quota.save(quota)
    return Response.success(data=request, msg="配额已更新")


@router.get("/audit", response_model=Response[ListAuditLogsResponse], dependencies=[Depends(require_admin)])
async def list_audit_logs(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        service: AuditService = Depends(get_audit_service),
) -> Response[ListAuditLogsResponse]:
    logs = await service.list_logs(limit=limit, offset=offset)
    return Response.success(data=ListAuditLogsResponse(logs=[AuditLogResponse.from_domain(log) for log in logs]))


@router.get("/audit/export", dependencies=[Depends(require_admin)])
async def export_audit_logs(service: AuditService = Depends(get_audit_service)) -> StreamingResponse:
    return StreamingResponse(
        service.export_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )


@router.get("/usage", response_model=Response[dict], dependencies=[Depends(require_admin)])
async def usage_summary(
        user_id: Optional[str] = Query(None),
        team_id: Optional[str] = Query(None),
        service: UsageStatsService = Depends(get_usage_stats_service),
) -> Response[dict]:
    return Response.success(data=await service.aggregate_usage(owner_user_id=user_id, team_id=team_id))


@router.get("/overview", response_model=Response[dict], dependencies=[Depends(require_admin)])
async def overview() -> Response[dict]:
    async with get_uow() as uow:
        users = await uow.user.list(limit=1)
    return Response.success(data={"has_users": bool(users)})
