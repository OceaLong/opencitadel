#!/usr/bin/env python
# -*- coding: utf-8 -*-
from fastapi import APIRouter, Depends

from app.application.services.team_service import TeamService
from app.interfaces.auth_dependencies import get_current_principal
from app.interfaces.schemas import Response
from app.interfaces.schemas.team import (
    CreateTeamInvitationRequest,
    CreateTeamRequest,
    InvitationLinkResponse,
    ListTeamMembersResponse,
    ListTeamsResponse,
    TeamMemberResponse,
    TeamResponse,
)
from app.interfaces.service_dependencies import get_team_service

router = APIRouter(prefix="/teams", tags=["团队模块"])
invitation_router = APIRouter(prefix="/invitations", tags=["邀请模块"])


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


@router.get("/{team_id}/members", response_model=Response[ListTeamMembersResponse])
async def list_members(
        team_id: str,
        principal=Depends(get_current_principal),
        service: TeamService = Depends(get_team_service),
) -> Response[ListTeamMembersResponse]:
    members = await service.list_members(team_id, principal.user_id)
    return Response.success(
        data=ListTeamMembersResponse(members=[TeamMemberResponse.from_domain(m) for m in members])
    )


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
    )
    return Response.success(data=InvitationLinkResponse(url=url), msg="邀请链接已生成")


@invitation_router.post("/{token}/accept", response_model=Response[TeamMemberResponse])
async def accept_invitation(
        token: str,
        principal=Depends(get_current_principal),
        service: TeamService = Depends(get_team_service),
) -> Response[TeamMemberResponse]:
    member = await service.accept_invitation(token=token, user_id=principal.user_id)
    return Response.success(data=TeamMemberResponse.from_domain(member), msg="已加入团队")
