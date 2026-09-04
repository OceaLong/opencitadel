import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import AfterValidator
from starlette.responses import StreamingResponse

from app.application.ports.crypto import ApplicationUrls
from app.application.ports.queries import ExecutionProjectionStatusPort
from app.application.services.audit_service import AuditService
from app.application.services.team_service import TeamService
from app.application.services.usage_stats_service import UsageBreakdownDimension, UsageStatsService
from app.domain.errors import BadRequestError, NotFoundError
from app.domain.models.audit_log import AuditLog
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.invitation import Invitation, InvitationType
from app.domain.models.user import UserStatus
from app.domain.models.user_quota import UserQuota
from app.domain.repositories.uow import UnitOfWorkFactory
from app.domain.utils.time_utils import to_utc
from app.interfaces.auth_dependencies import (
    get_current_principal,
    require_admin,
    require_auditor_or_admin,
)
from app.interfaces.client_ip import get_client_ip
from app.interfaces.schemas import Response
from app.interfaces.schemas.admin import (
    AdminOverviewResponse,
    AdminTeamResponse,
    AdminUserResponse,
    AuditLogDetailResponse,
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
from app.interfaces.service_dependencies import (
    get_application_urls,
    get_audit_service,
    get_execution_projection_status,
    get_team_service,
    get_uow_factory,
    get_usage_stats_service,
)

# HTTP 时间参数必须携带时区，并在进入服务层前规范化为 UTC。
UtcDatetime = Annotated[datetime | None, Query(), AfterValidator(to_utc)]

router = APIRouter(prefix="/admin", tags=["管理员"])


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
            actor_ip=get_client_ip(request),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request.headers.get("x-request-id") or "",
            metadata=metadata or {},
        ),
    )


@router.get(
    "/users", response_model=Response[ListAdminUsersResponse], dependencies=[Depends(require_admin)]
)
async def list_users(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response[ListAdminUsersResponse]:
    async with uow_factory() as uow:
        users = await uow.user.list(limit=limit, offset=offset)
        total = await uow.user.count()
    return Response.success(
        data=ListAdminUsersResponse(
            users=[AdminUserResponse.from_domain(u) for u in users],
            total=total,
        ),
    )


@router.patch(
    "/users/{user_id}",
    response_model=Response[AdminUserResponse],
    dependencies=[Depends(require_admin)],
)
async def patch_user(
    user_id: str,
    request_body: PatchUserRequest,
    request: Request,
    audit_service: AuditService = Depends(get_audit_service),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response[AdminUserResponse]:
    principal = await get_current_principal()
    async with uow_factory() as uow:
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
        user.updated_at = datetime.now(UTC)
        await uow.user.save(user)
        await uow.commit()
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


@router.delete(
    "/users/{user_id}", response_model=Response[dict], dependencies=[Depends(require_admin)]
)
async def delete_user(
    user_id: str,
    request: Request,
    strategy: str = Query("anonymize", pattern="^(cascade|anonymize|transfer_to_team)$"),
    team_id: str | None = Query(None),
    audit_service: AuditService = Depends(get_audit_service),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response[dict]:
    principal = await get_current_principal()
    if strategy == "transfer_to_team" and not team_id:
        raise BadRequestError(
            "transfer_to_team 策略需要指定目标团队 team_id",
            error_key="errors.transferTeamRequired",
        )
    moved_resources = 0
    # Reassigning another user's resources to a team crosses the personal-owner
    # RLS predicate, so the transfer strategy runs under a system scope; the
    # existing cascade/anonymize paths keep their ambient admin scope.
    authorization_context = (
        AuthorizationContext.system("admin-user-delete") if strategy == "transfer_to_team" else None
    )
    async with uow_factory(authorization_context=authorization_context) as uow:
        user = await uow.user.get_by_id(user_id)
        if not user:
            raise NotFoundError("用户不存在")
        if strategy == "cascade":
            await uow.user.delete_owned_resources(user_id)
            await uow.user.delete_by_id(user_id)
        elif strategy == "transfer_to_team":
            team = await uow.team.get_by_id(team_id or "")
            if not team:
                raise NotFoundError("团队不存在")
            moved_resources = await uow.user.transfer_personal_resources_to_team(user_id, team.id)
            user.status = UserStatus.DISABLED
            user.token_version += 1
            user.email = f"deleted-{user.id}@deleted.local"
            user.username = f"deleted-{user.id}"
            user.display_name = "Deleted User"
            await uow.user.save(user)
            await uow.refresh_token.revoke_all_for_user(user.id)
            await uow.user.revoke_security_material(user.id)
        else:
            user.status = UserStatus.DISABLED
            user.token_version += 1
            if strategy == "anonymize":
                user.email = f"deleted-{user.id}@deleted.local"
                user.username = f"deleted-{user.id}"
                user.display_name = "Deleted User"
            await uow.user.save(user)
            await uow.refresh_token.revoke_all_for_user(user.id)
            await uow.user.revoke_security_material(user.id)
        await uow.commit()
    metadata: dict = {"strategy": strategy}
    if strategy == "transfer_to_team":
        metadata["team_id"] = team_id
        metadata["moved_resources"] = moved_resources
    await _record_admin_audit(
        audit_service,
        actor_user_id=principal.user_id,
        action="admin.user.delete",
        resource_type="user",
        resource_id=user_id,
        request=request,
        metadata=metadata,
    )
    return Response.success(data={"strategy": strategy})


@router.post(
    "/invitations",
    response_model=Response[InvitationLinkResponse],
    dependencies=[Depends(require_admin)],
)
async def create_platform_invitation(
    request_body: CreatePlatformInvitationRequest,
    request: Request,
    audit_service: AuditService = Depends(get_audit_service),
    application_urls: ApplicationUrls = Depends(get_application_urls),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response[InvitationLinkResponse]:
    principal = await get_current_principal()
    token = secrets.token_urlsafe(32)
    invitation = Invitation(
        type=InvitationType.PLATFORM,
        email=request_body.email.strip().lower(),
        token=token,
        invited_by=principal.user_id,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    async with uow_factory() as uow:
        await uow.invitation.save(invitation)
        await uow.commit()
    url = f"{application_urls.frontend_base_url.rstrip('/')}/register?invite_token={token}"
    await _record_admin_audit(
        audit_service,
        actor_user_id=principal.user_id,
        action="admin.invitation.create",
        resource_type="invitation",
        resource_id=invitation.id,
        request=request,
        metadata={"email": invitation.email},
    )
    return Response.success(data=InvitationLinkResponse(url=url))


@router.get(
    "/invitations",
    response_model=Response[ListPlatformInvitationsResponse],
    dependencies=[Depends(require_admin)],
)
async def list_platform_invitations(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response[ListPlatformInvitationsResponse]:
    now = datetime.now(UTC)
    async with uow_factory() as uow:
        invitations = await uow.invitation.list(
            invitation_type=InvitationType.PLATFORM,
            limit=limit,
            offset=offset,
        )
        total = await uow.invitation.count(invitation_type=InvitationType.PLATFORM)
    return Response.success(
        data=ListPlatformInvitationsResponse(
            invitations=[
                PlatformInvitationResponse.from_domain(item, now=now) for item in invitations
            ],
            total=total,
        ),
    )


@router.get(
    "/users/{user_id}/quota",
    response_model=Response[QuotaRequest],
    dependencies=[Depends(require_admin)],
)
async def get_quota(
    user_id: str,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response[QuotaRequest]:
    async with uow_factory() as uow:
        quota = await uow.quota.get_for_user(user_id)
    return Response.success(data=QuotaRequest(**quota.model_dump()) if quota else QuotaRequest())


@router.put(
    "/users/{user_id}/quota",
    response_model=Response[QuotaRequest],
    dependencies=[Depends(require_admin)],
)
async def put_quota(
    user_id: str,
    request_body: QuotaRequest,
    request: Request,
    audit_service: AuditService = Depends(get_audit_service),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response[QuotaRequest]:
    principal = await get_current_principal()
    quota = UserQuota(user_id=user_id, **request_body.model_dump())
    async with uow_factory() as uow:
        await uow.quota.save(quota)
        await uow.commit()
    await _record_admin_audit(
        audit_service,
        actor_user_id=principal.user_id,
        action="admin.user.quota.update",
        resource_type="user",
        resource_id=user_id,
        request=request,
        metadata=request_body.model_dump(exclude_none=True),
    )
    return Response.success(data=request_body)


@router.get(
    "/audit",
    response_model=Response[ListAuditLogsResponse],
    dependencies=[Depends(require_auditor_or_admin)],
)
async def list_audit_logs(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None),
    actor_user_id: str | None = Query(None),
    resource_type: str | None = Query(None),
    resource_id: str | None = Query(None),
    session_id: str | None = Query(None),
    start_at: UtcDatetime = None,
    end_at: UtcDatetime = None,
    service: AuditService = Depends(get_audit_service),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response[ListAuditLogsResponse]:
    logs = await service.list_logs(
        action=action,
        actor_user_id=actor_user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        session_id=session_id,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        offset=offset,
    )
    async with uow_factory() as uow:
        total = await uow.audit.count(
            action=action,
            actor_user_id=actor_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            session_id=session_id,
            start_at=start_at,
            end_at=end_at,
        )
    return Response.success(
        data=ListAuditLogsResponse(
            logs=[AuditLogResponse.from_domain(log) for log in logs],
            total=total,
        ),
    )


@router.get(
    "/audit/logs/{log_id}",
    response_model=Response[AuditLogDetailResponse],
    dependencies=[Depends(require_auditor_or_admin)],
)
async def get_audit_log(
    log_id: str,
    service: AuditService = Depends(get_audit_service),
) -> Response[AuditLogDetailResponse]:
    log = await service.get_log(log_id)
    if not log:
        raise NotFoundError("审计记录不存在")
    return Response.success(data=AuditLogDetailResponse.from_domain(log))


@router.get(
    "/audit/summary",
    response_model=Response[AuditSummaryResponse],
    dependencies=[Depends(require_auditor_or_admin)],
)
async def audit_summary(
    start_at: UtcDatetime = None,
    end_at: UtcDatetime = None,
    service: AuditService = Depends(get_audit_service),
) -> Response[AuditSummaryResponse]:
    summary = await service.summarize(start_at=start_at, end_at=end_at)
    return Response.success(data=AuditSummaryResponse(**summary))


@router.get("/audit/export", dependencies=[Depends(require_auditor_or_admin)])
async def export_audit_logs(
    action: str | None = Query(None),
    actor_user_id: str | None = Query(None),
    resource_type: str | None = Query(None),
    resource_id: str | None = Query(None),
    session_id: str | None = Query(None),
    start_at: UtcDatetime = None,
    end_at: UtcDatetime = None,
    service: AuditService = Depends(get_audit_service),
) -> StreamingResponse:
    return StreamingResponse(
        service.export_csv(
            action=action,
            actor_user_id=actor_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            session_id=session_id,
            start_at=start_at,
            end_at=end_at,
        ),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )


@router.get(
    "/usage",
    response_model=Response[UsageSummaryResponse],
    dependencies=[Depends(require_auditor_or_admin)],
)
async def usage_summary(
    user_id: str | None = Query(None),
    team_id: str | None = Query(None),
    start_at: UtcDatetime = None,
    end_at: UtcDatetime = None,
    service: UsageStatsService = Depends(get_usage_stats_service),
) -> Response[UsageSummaryResponse]:
    data = await service.aggregate_usage(
        owner_user_id=user_id,
        team_id=team_id,
        start_at=start_at,
        end_at=end_at,
    )
    return Response.success(data=UsageSummaryResponse(**data))


@router.get(
    "/usage/summary",
    response_model=Response[UsageSummaryResponse],
    dependencies=[Depends(require_auditor_or_admin)],
)
async def usage_summary_alias(
    user_id: str | None = Query(None),
    team_id: str | None = Query(None),
    start_at: UtcDatetime = None,
    end_at: UtcDatetime = None,
    service: UsageStatsService = Depends(get_usage_stats_service),
) -> Response[UsageSummaryResponse]:
    return await usage_summary(
        user_id=user_id, team_id=team_id, start_at=start_at, end_at=end_at, service=service
    )


@router.get(
    "/usage/timeseries",
    response_model=Response[UsageTimeseriesResponse],
    dependencies=[Depends(require_auditor_or_admin)],
)
async def usage_timeseries(
    user_id: str | None = Query(None),
    team_id: str | None = Query(None),
    start_at: UtcDatetime = None,
    end_at: UtcDatetime = None,
    service: UsageStatsService = Depends(get_usage_stats_service),
) -> Response[UsageTimeseriesResponse]:
    points = await service.usage_timeseries(
        owner_user_id=user_id,
        team_id=team_id,
        start_at=start_at,
        end_at=end_at,
    )
    return Response.success(data=UsageTimeseriesResponse(points=points))


@router.get(
    "/usage/breakdown",
    response_model=Response[UsageBreakdownResponse],
    dependencies=[Depends(require_auditor_or_admin)],
)
async def usage_breakdown(
    dimension: UsageBreakdownDimension = Query("model"),
    user_id: str | None = Query(None),
    team_id: str | None = Query(None),
    start_at: UtcDatetime = None,
    end_at: UtcDatetime = None,
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


@router.get(
    "/overview",
    response_model=Response[AdminOverviewResponse],
    dependencies=[Depends(require_auditor_or_admin)],
)
async def overview(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response[AdminOverviewResponse]:
    now = datetime.now(UTC)
    async with uow_factory() as uow:
        total_users = await uow.user.count()
        status_counts = await uow.user.count_by_status()
        role_counts = await uow.user.count_by_role()
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
            active_users=status_counts.get(UserStatus.ACTIVE.value, 0),
            disabled_users=status_counts.get(UserStatus.DISABLED.value, 0),
            admin_users=role_counts.get("admin", 0),
            pending_invitations=pending,
            accepted_invitations=accepted,
            expired_invitations=expired,
            total_teams=total_teams,
            total_sessions=total_sessions,
        ),
    )


@router.get(
    "/teams", response_model=Response[ListAdminTeamsResponse], dependencies=[Depends(require_admin)]
)
async def list_teams(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    team_service: TeamService = Depends(get_team_service),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response[ListAdminTeamsResponse]:
    teams, total = await team_service.admin_list_all(limit=limit, offset=offset)
    async with uow_factory() as uow:
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


@router.delete(
    "/teams/{team_id}", response_model=Response[None], dependencies=[Depends(require_admin)]
)
async def delete_team_admin(
    team_id: str,
    request: Request,
    strategy: str = Query("transfer_to_owner", pattern="^(cascade|transfer_to_owner)$"),
    principal=Depends(get_current_principal),
    team_service: TeamService = Depends(get_team_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> Response[None]:
    result = await team_service.admin_delete_team(team_id, strategy=strategy)
    await _record_admin_audit(
        audit_service,
        actor_user_id=principal.user_id,
        action="admin.team.delete",
        resource_type="team",
        resource_id=team_id,
        request=request,
        metadata={
            "strategy": result.strategy,
            "affected_resources": result.affected_resources,
            "transferred_to_user_id": result.transferred_to_user_id,
        },
    )
    return Response.success()


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
    return Response.success()


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
    return Response.success(data=TeamMemberResponse.from_domain(member))


@router.get(
    "/execution/projection-status",
    response_model=Response[dict],
    dependencies=[Depends(require_admin)],
    summary="执行投影状态（每 scope 滞后 + 隔离清单）",
)
async def get_execution_projection_status_admin(
    lag_limit: int = Query(100, ge=1, le=1000),
    projection_status: ExecutionProjectionStatusPort = Depends(get_execution_projection_status),
) -> Response[dict]:
    """D13/K4-3: 管理员可见的正式投影运行状况。

    - ``scope_lags``：scope head 水位 − formal checkpoint 的每 scope 滞后（仅列出滞后 > 0 的 scope，按滞后降序）。
    - ``poisoned_scopes``：连续失败被隔离或正在重建中的 scope 清单。
    """
    lags = await projection_status.scope_lags(limit=lag_limit)
    poisoned = await projection_status.poisoned_scopes()
    return Response.success(
        data={
            "scope_lags": [
                {
                    "owner_scope_key": item.owner_scope_key,
                    "head_position": item.head_position,
                    "checkpoint_position": item.checkpoint_position,
                    "lag": item.lag,
                }
                for item in lags
            ],
            "poisoned_scopes": [
                {
                    "owner_scope_key": item.owner_scope_key,
                    "reason": item.reason,
                    "last_error": item.last_error,
                    "failure_count": item.failure_count,
                    "rebuilding": item.rebuilding,
                    "first_seen_at": item.first_seen_at.isoformat(),
                    "last_seen_at": item.last_seen_at.isoformat(),
                }
                for item in poisoned
            ],
        }
    )
