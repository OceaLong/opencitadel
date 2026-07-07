#!/usr/bin/env python
# -*- coding: utf-8 -*-
from fastapi import APIRouter, Depends, Request, Response as StarletteResponse

from app.application.services.auth_service import AuthService
from app.application.services.team_service import TeamService
from app.interfaces.auth_dependencies import get_current_principal
from app.interfaces.schemas import Response
from app.interfaces.schemas.team import (
    CreateTeamInvitationRequest,
    CreateTeamRequest,
    InvitationLinkResponse,
    ListTeamMemberDetailsResponse,
    ListTeamsResponse,
    TeamInvitationPreviewResponse,
    TeamInvitationRegisterRequest,
    TeamMemberResponse,
    TeamResponse,
    UpdateTeamMemberRoleRequest,
)
from app.interfaces.service_dependencies import get_auth_service, get_cookie_manager, get_team_service
from app.infrastructure.security.cookie import AuthCookieManager

router = APIRouter(prefix="/teams", tags=["团队模块"])
invitation_router = APIRouter(prefix="/invitations", tags=["邀请模块"])
public_invitation_router = APIRouter(prefix="/invitations", tags=["邀请模块"])


@router.post("", response_model=Response[TeamResponse])
async def create_team(
        request: CreateTeamRequest,
        principal=Depends(get_current_principal),
        service: TeamService = Depends(get_team_service),
) -> Response[TeamResponse]:
    team = await service.create_team(
        name=request.name,
        description=request.description,
        actor_user_id=principal.user_id,
    )
    return Response.success(data=TeamResponse.from_domain(team), msg="创建团队成功")


@router.get("", response_model=Response[ListTeamsResponse])
async def list_my_teams(
        principal=Depends(get_current_principal),
        service: TeamService = Depends(get_team_service),
) -> Response[ListTeamsResponse]:
    teams = await service.list_my_teams(principal.user_id)
    return Response.success(data=ListTeamsResponse(teams=[TeamResponse.from_domain(t) for t in teams]))


@router.get("/{team_id}", response_model=Response[TeamResponse])
async def get_team(
        team_id: str,
        principal=Depends(get_current_principal),
        service: TeamService = Depends(get_team_service),
) -> Response[TeamResponse]:
    team = await service.get_team(team_id, principal.user_id)
    return Response.success(data=TeamResponse.from_domain(team))


@router.get("/{team_id}/members", response_model=Response[ListTeamMemberDetailsResponse])
async def list_members(
        team_id: str,
        principal=Depends(get_current_principal),
        service: TeamService = Depends(get_team_service),
) -> Response[ListTeamMemberDetailsResponse]:
    members = await service.list_member_details(team_id, principal.user_id)
    return Response.success(data=ListTeamMemberDetailsResponse(members=members))


@router.post("/{team_id}/leave", response_model=Response[None])
async def leave_team(
        team_id: str,
        principal=Depends(get_current_principal),
        service: TeamService = Depends(get_team_service),
) -> Response[None]:
    await service.leave_team(team_id=team_id, user_id=principal.user_id)
    return Response.success(msg="已退出团队")


@router.post("/{team_id}/invitations", response_model=Response[InvitationLinkResponse])
async def create_team_invitation(
        team_id: str,
        request: CreateTeamInvitationRequest,
        principal=Depends(get_current_principal),
        service: TeamService = Depends(get_team_service),
) -> Response[InvitationLinkResponse]:
    url = await service.create_team_invitation(
        team_id=team_id,
        actor_user_id=principal.user_id,
        role=request.role,
        email=request.email,
    )
    return Response.success(data=InvitationLinkResponse(url=url), msg="邀请链接已生成")


@public_invitation_router.get("/{token}", response_model=Response[TeamInvitationPreviewResponse])
async def preview_invitation(
        token: str,
        service: TeamService = Depends(get_team_service),
) -> Response[TeamInvitationPreviewResponse]:
    preview = await service.preview_invitation(token=token)
    return Response.success(data=preview)


@public_invitation_router.post("/{token}/register", response_model=Response[TeamMemberResponse])
async def register_and_accept_invitation(
        token: str,
        request: TeamInvitationRegisterRequest,
        response: StarletteResponse,
        http_request: Request,
        service: TeamService = Depends(get_team_service),
        auth_service: AuthService = Depends(get_auth_service),
        cookie_manager: AuthCookieManager = Depends(get_cookie_manager),
) -> Response[TeamMemberResponse]:
    from app.interfaces.endpoints.auth_routes import _client_ip

    result = await service.register_and_accept_invitation(
        token=token,
        email=request.email,
        username=request.username,
        password=request.password,
    )
    user, tokens = await auth_service.login(
        email_or_username=result.user.email,
        password=request.password,
        user_agent=http_request.headers.get("user-agent", ""),
        ip_address=_client_ip(http_request),
    )
    cookie_manager.set_auth_cookies(response, access_token=tokens.access_token, refresh_token=tokens.refresh_token)
    return Response.success(
        data=TeamMemberResponse.from_domain(result.member),
        msg="注册成功并已加入团队",
    )


@invitation_router.post("/{token}/accept", response_model=Response[TeamMemberResponse])
async def accept_invitation(
        token: str,
        principal=Depends(get_current_principal),
        service: TeamService = Depends(get_team_service),
) -> Response[TeamMemberResponse]:
    member = await service.accept_invitation(token=token, user_id=principal.user_id)
    return Response.success(data=TeamMemberResponse.from_domain(member), msg="已加入团队")


@router.delete("/{team_id}", response_model=Response[None])
async def delete_team(
        team_id: str,
        principal=Depends(get_current_principal),
        service: TeamService = Depends(get_team_service),
) -> Response[None]:
    await service.delete_team(team_id=team_id, actor_user_id=principal.user_id)
    return Response.success(msg="团队已解散")


@router.delete("/{team_id}/members/{user_id}", response_model=Response[None])
async def remove_member(
        team_id: str,
        user_id: str,
        principal=Depends(get_current_principal),
        service: TeamService = Depends(get_team_service),
) -> Response[None]:
    await service.remove_member(team_id=team_id, actor_user_id=principal.user_id, target_user_id=user_id)
    return Response.success(msg="成员已移除")


@router.patch("/{team_id}/members/{user_id}", response_model=Response[TeamMemberResponse])
async def update_member_role(
        team_id: str,
        user_id: str,
        request: UpdateTeamMemberRoleRequest,
        principal=Depends(get_current_principal),
        service: TeamService = Depends(get_team_service),
) -> Response[TeamMemberResponse]:
    member = await service.update_member_role(
        team_id=team_id,
        actor_user_id=principal.user_id,
        target_user_id=user_id,
        role=request.role,
    )
    return Response.success(data=TeamMemberResponse.from_domain(member), msg="成员角色已更新")
