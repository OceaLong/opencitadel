#!/usr/bin/env python
# -*- coding: utf-8 -*-
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, text
from starlette.responses import StreamingResponse

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.application.services.audit_service import AuditService
from app.application.services.team_service import TeamService
from app.application.services.usage_stats_service import UsageBreakdownDimension, UsageStatsService
from app.domain.models.audit_log import AuditLog
from app.domain.models.invitation import Invitation, InvitationType
from app.domain.models.user import UserStatus
from app.domain.models.user_quota import UserQuota
from app.interfaces.auth_dependencies import get_current_principal, require_admin, require_auditor_or_admin
from app.interfaces.schemas import Response
from app.interfaces.schemas.admin import (
    AdminOverviewResponse,
    AdminTeamResponse,
    AdminUserResponse,
    AuditLogResponse,
    AuditSummaryResponse,
    CreatePlatformInvitationRequest,
    InvitationStatus,
    ListAdminTeamsResponse,
    ListAdminUsersResponse,
    ListAuditLogsResponse,
    ListPlatformInvitationsResponse,
    PatchUserRequest,
    PlatformInvitationResponse,
    QuotaRequest,
    UsageBreakdownResponse,
    UsageSummaryResponse,
    UsageTimeseriesResponse,
)
from app.interfaces.schemas.team import (
    InvitationLinkResponse,
    ListTeamMemberDetailsResponse,
    TeamMemberResponse,
    UpdateTeamMemberRoleRequest,
)
from app.interfaces.service_dependencies import get_audit_service, get_team_service, get_usage_stats_service
from app.infrastructure.models.user import UserORM
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


async def _record_admin_audit(
        audit_service: AuditService,
        *,
        actor_user_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        request: Request,
        metadata: dict | None = None,
) -> None:
    await audit_service.record(
        AuditLog(
            actor_user_id=actor_user_id,
            actor_ip=request.client.host if request.client else "",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request.headers.get("x-request-id") or "",
            metadata=metadata or {},
        ),
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
        total = await uow.user.count()
    return Response.success(
        data=ListAdminUsersResponse(
            users=[AdminUserResponse.from_domain(u) for u in users],
            total=total,
        ),
    )


@router.patch("/users/{user_id}", response_model=Response[AdminUserResponse], dependencies=[Depends(require_admin)])
async def patch_user(
        user_id: str,
        request_body: PatchUserRequest,
        request: Request,
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[AdminUserResponse]:
    principal = await get_current_principal()
    async with get_uow() as uow:
        user = await uow.user.get_by_id(user_id)
        if not user:
            raise NotFoundError("用户不存在")
        if request_body.global_role is not None:
            user.global_role = request_body.global_role
        if request_body.status is not None:
            user.status = request_body.status
            user.token_version += 1
            await uow.refresh_token.revoke_all_for_user(user.id)
        if request_body.display_name is not None:
            user.display_name = request_body.display_name
        user.updated_at = datetime.now()
        await uow.user.save(user)
    await _record_admin_audit(
        audit_service,
        actor_user_id=principal.user_id,
        action="admin.user.patch",
        resource_type="user",
        resource_id=user_id,
        request=request,
        metadata=request_body.model_dump(exclude_none=True),
    )
    return Response.success(data=AdminUserResponse.from_domain(user))


@router.delete("/users/{user_id}", response_model=Response[dict], dependencies=[Depends(require_admin)])
async def delete_user(
        user_id: str,
        request: Request,
        strategy: str = Query("anonymize", pattern="^(cascade|transfer_to_team|anonymize)$"),
        target_team_id: Optional[str] = Query(None),
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[dict]:
    principal = await get_current_principal()
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
    await _record_admin_audit(
        audit_service,
        actor_user_id=principal.user_id,
        action="admin.user.delete",
        resource_type="user",
        resource_id=user_id,
        request=request,
        metadata={"strategy": strategy, "target_team_id": target_team_id},
    )
    return Response.success(msg="用户删除策略已执行", data={"strategy": strategy})


@router.post("/invitations", response_model=Response[InvitationLinkResponse], dependencies=[Depends(require_admin)])
async def create_platform_invitation(
        request_body: CreatePlatformInvitationRequest,
        request: Request,
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[InvitationLinkResponse]:
    principal = await get_current_principal()
    token = secrets.token_urlsafe(32)
    invitation = Invitation(
        type=InvitationType.PLATFORM,
        email=request_body.email.strip().lower(),
        token=token,
        invited_by=principal.user_id,
        expires_at=datetime.now() + timedelta(days=7),
    )
    async with get_uow() as uow:
        await uow.invitation.save(invitation)
    url = f"{get_settings().frontend_base_url.rstrip('/')}/register?invite_token={token}"
    await _record_admin_audit(
        audit_service,
        actor_user_id=principal.user_id,
        action="admin.invitation.create",
        resource_type="invitation",
        resource_id=invitation.id,
        request=request,
        metadata={"email": invitation.email},
    )
    return Response.success(data=InvitationLinkResponse(url=url), msg="平台邀请链接已生成")


@router.get(
    "/invitations",
    response_model=Response[ListPlatformInvitationsResponse],
    dependencies=[Depends(require_admin)],
)
async def list_platform_invitations(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
) -> Response[ListPlatformInvitationsResponse]:
    now = datetime.now()
    async with get_uow() as uow:
        invitations = await uow.invitation.list(
            invitation_type=InvitationType.PLATFORM,
            limit=limit,
            offset=offset,
        )
        total = await uow.invitation.count(invitation_type=InvitationType.PLATFORM)
    return Response.success(
        data=ListPlatformInvitationsResponse(
            invitations=[PlatformInvitationResponse.from_domain(item, now=now) for item in invitations],
            total=total,
        ),
    )


@router.get("/users/{user_id}/quota", response_model=Response[QuotaRequest], dependencies=[Depends(require_admin)])
async def get_quota(user_id: str) -> Response[QuotaRequest]:
    async with get_uow() as uow:
        quota = await uow.quota.get_for_user(user_id)
    return Response.success(data=QuotaRequest(**quota.model_dump()) if quota else QuotaRequest())


@router.put("/users/{user_id}/quota", response_model=Response[QuotaRequest], dependencies=[Depends(require_admin)])
async def put_quota(
        user_id: str,
        request_body: QuotaRequest,
        request: Request,
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[QuotaRequest]:
    principal = await get_current_principal()
    quota = UserQuota(user_id=user_id, **request_body.model_dump())
    async with get_uow() as uow:
        await uow.quota.save(quota)
    await _record_admin_audit(
        audit_service,
        actor_user_id=principal.user_id,
        action="admin.user.quota.update",
        resource_type="user",
        resource_id=user_id,
        request=request,
        metadata=request_body.model_dump(exclude_none=True),
    )
    return Response.success(data=request_body, msg="配额已更新")


@router.get("/audit", response_model=Response[ListAuditLogsResponse], dependencies=[Depends(require_auditor_or_admin)])
async def list_audit_logs(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        action: Optional[str] = Query(None),
        start_at: Optional[datetime] = Query(None),
        end_at: Optional[datetime] = Query(None),
        service: AuditService = Depends(get_audit_service),
) -> Response[ListAuditLogsResponse]:
    logs = await service.list_logs(
        action=action,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        offset=offset,
    )
    async with get_uow() as uow:
        total = await uow.audit.count(action=action, start_at=start_at, end_at=end_at)
    return Response.success(
        data=ListAuditLogsResponse(
            logs=[AuditLogResponse.from_domain(log) for log in logs],
            total=total,
        ),
    )


@router.get("/audit/summary", response_model=Response[AuditSummaryResponse], dependencies=[Depends(require_auditor_or_admin)])
async def audit_summary(
        start_at: Optional[datetime] = Query(None),
        end_at: Optional[datetime] = Query(None),
        service: AuditService = Depends(get_audit_service),
) -> Response[AuditSummaryResponse]:
    summary = await service.summarize(start_at=start_at, end_at=end_at)
    return Response.success(data=AuditSummaryResponse(**summary))


@router.get("/audit/export", dependencies=[Depends(require_auditor_or_admin)])
async def export_audit_logs(service: AuditService = Depends(get_audit_service)) -> StreamingResponse:
    return StreamingResponse(
        service.export_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )


@router.get("/usage", response_model=Response[UsageSummaryResponse], dependencies=[Depends(require_auditor_or_admin)])
async def usage_summary(
        user_id: Optional[str] = Query(None),
        team_id: Optional[str] = Query(None),
        start_at: Optional[datetime] = Query(None),
        end_at: Optional[datetime] = Query(None),
        service: UsageStatsService = Depends(get_usage_stats_service),
) -> Response[UsageSummaryResponse]:
    data = await service.aggregate_usage(
        owner_user_id=user_id,
        team_id=team_id,
        start_at=start_at,
        end_at=end_at,
    )
    return Response.success(data=UsageSummaryResponse(**data))


@router.get("/usage/summary", response_model=Response[UsageSummaryResponse], dependencies=[Depends(require_auditor_or_admin)])
async def usage_summary_alias(
        user_id: Optional[str] = Query(None),
        team_id: Optional[str] = Query(None),
        start_at: Optional[datetime] = Query(None),
        end_at: Optional[datetime] = Query(None),
        service: UsageStatsService = Depends(get_usage_stats_service),
) -> Response[UsageSummaryResponse]:
    return await usage_summary(user_id=user_id, team_id=team_id, start_at=start_at, end_at=end_at, service=service)


@router.get("/usage/timeseries", response_model=Response[UsageTimeseriesResponse], dependencies=[Depends(require_auditor_or_admin)])
async def usage_timeseries(
        user_id: Optional[str] = Query(None),
        team_id: Optional[str] = Query(None),
        start_at: Optional[datetime] = Query(None),
        end_at: Optional[datetime] = Query(None),
        service: UsageStatsService = Depends(get_usage_stats_service),
) -> Response[UsageTimeseriesResponse]:
    points = await service.usage_timeseries(
        owner_user_id=user_id,
        team_id=team_id,
        start_at=start_at,
        end_at=end_at,
    )
    return Response.success(data=UsageTimeseriesResponse(points=points))


@router.get("/usage/breakdown", response_model=Response[UsageBreakdownResponse], dependencies=[Depends(require_auditor_or_admin)])
async def usage_breakdown(
        dimension: UsageBreakdownDimension = Query("model"),
        user_id: Optional[str] = Query(None),
        team_id: Optional[str] = Query(None),
        start_at: Optional[datetime] = Query(None),
        end_at: Optional[datetime] = Query(None),
        limit: int = Query(10, ge=1, le=50),
        service: UsageStatsService = Depends(get_usage_stats_service),
) -> Response[UsageBreakdownResponse]:
    items = await service.usage_breakdown(
        dimension=dimension,
        owner_user_id=user_id,
        team_id=team_id,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
    )
    return Response.success(data=UsageBreakdownResponse(dimension=dimension, items=items))


@router.get("/overview", response_model=Response[AdminOverviewResponse], dependencies=[Depends(require_auditor_or_admin)])
async def overview() -> Response[AdminOverviewResponse]:
    now = datetime.now()
    async with get_uow() as uow:
        total_users = await uow.user.count()
        active_users_result = await uow.db_session.execute(
            select(func.count()).select_from(UserORM).where(UserORM.status == UserStatus.ACTIVE.value),
        )
        disabled_users_result = await uow.db_session.execute(
            select(func.count()).select_from(UserORM).where(UserORM.status == UserStatus.DISABLED.value),
        )
        admin_users_result = await uow.db_session.execute(
            select(func.count()).select_from(UserORM).where(UserORM.global_role == "admin"),
        )
        invitations = await uow.invitation.list(invitation_type=InvitationType.PLATFORM, limit=500)
        total_teams = await uow.team.count()
        total_sessions = await uow.session.count()
    pending = accepted = expired = 0
    for invitation in invitations:
        status = PlatformInvitationResponse.from_domain(invitation, now=now).status
        if status == InvitationStatus.PENDING:
            pending += 1
        elif status == InvitationStatus.ACCEPTED:
            accepted += 1
        else:
            expired += 1
    return Response.success(
        data=AdminOverviewResponse(
            total_users=total_users,
            active_users=int(active_users_result.scalar_one() or 0),
            disabled_users=int(disabled_users_result.scalar_one() or 0),
            admin_users=int(admin_users_result.scalar_one() or 0),
            pending_invitations=pending,
            accepted_invitations=accepted,
            expired_invitations=expired,
            total_teams=total_teams,
            total_sessions=total_sessions,
        ),
    )


@router.get("/teams", response_model=Response[ListAdminTeamsResponse], dependencies=[Depends(require_admin)])
async def list_teams(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        team_service: TeamService = Depends(get_team_service),
) -> Response[ListAdminTeamsResponse]:
    teams, total = await team_service.admin_list_all(limit=limit, offset=offset)
    async with get_uow() as uow:
        member_counts = await uow.team.count_members_by_teams([team.id for team in teams])
    return Response.success(
        data=ListAdminTeamsResponse(
            teams=[
                AdminTeamResponse(
                    id=team.id,
                    name=team.name,
                    description=team.description,
                    created_by=team.created_by,
                    created_at=team.created_at,
                    member_count=member_counts.get(team.id, 0),
                )
                for team in teams
            ],
            total=total,
        ),
    )


@router.get(
    "/teams/{team_id}/members",
    response_model=Response[ListTeamMemberDetailsResponse],
    dependencies=[Depends(require_admin)],
)
async def list_team_members_admin(
        team_id: str,
        team_service: TeamService = Depends(get_team_service),
) -> Response[ListTeamMemberDetailsResponse]:
    members = await team_service.admin_list_member_details(team_id)
    return Response.success(data=ListTeamMemberDetailsResponse(members=members))


@router.delete("/teams/{team_id}", response_model=Response[None], dependencies=[Depends(require_admin)])
async def delete_team_admin(
        team_id: str,
        request: Request,
        principal=Depends(get_current_principal),
        team_service: TeamService = Depends(get_team_service),
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[None]:
    await team_service.admin_delete_team(team_id)
    await _record_admin_audit(
        audit_service,
        actor_user_id=principal.user_id,
        action="admin.team.delete",
        resource_type="team",
        resource_id=team_id,
        request=request,
    )
    return Response.success(msg="团队已解散")


@router.delete(
    "/teams/{team_id}/members/{user_id}",
    response_model=Response[None],
    dependencies=[Depends(require_admin)],
)
async def remove_team_member_admin(
        team_id: str,
        user_id: str,
        request: Request,
        principal=Depends(get_current_principal),
        team_service: TeamService = Depends(get_team_service),
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[None]:
    await team_service.admin_remove_member(team_id, user_id)
    await _record_admin_audit(
        audit_service,
        actor_user_id=principal.user_id,
        action="admin.team.member.remove",
        resource_type="team_member",
        resource_id=f"{team_id}:{user_id}",
        request=request,
        metadata={"team_id": team_id, "user_id": user_id},
    )
    return Response.success(msg="成员已移除")


@router.patch(
    "/teams/{team_id}/members/{user_id}",
    response_model=Response[TeamMemberResponse],
    dependencies=[Depends(require_admin)],
)
async def update_team_member_role_admin(
        team_id: str,
        user_id: str,
        request_body: UpdateTeamMemberRoleRequest,
        request: Request,
        principal=Depends(get_current_principal),
        team_service: TeamService = Depends(get_team_service),
        audit_service: AuditService = Depends(get_audit_service),
) -> Response[TeamMemberResponse]:
    member = await team_service.admin_update_member_role(team_id, user_id, request_body.role)
    await _record_admin_audit(
        audit_service,
        actor_user_id=principal.user_id,
        action="admin.team.member.role",
        resource_type="team_member",
        resource_id=f"{team_id}:{user_id}",
        request=request,
        metadata={"team_id": team_id, "user_id": user_id, "role": request_body.role.value},
    )
    return Response.success(data=TeamMemberResponse.from_domain(member), msg="成员角色已更新")
