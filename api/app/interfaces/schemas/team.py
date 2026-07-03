#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime

from pydantic import BaseModel

from app.domain.models.team import Team, TeamMember, TeamRole


class CreateTeamRequest(BaseModel):
    name: str
    description: str = ""


class TeamResponse(BaseModel):
    id: str
    name: str
    description: str
    created_by: str | None
    created_at: datetime

    @classmethod
    def from_domain(cls, team: Team) -> "TeamResponse":
        return cls(**team.model_dump())


class ListTeamsResponse(BaseModel):
    teams: list[TeamResponse]


class TeamMemberResponse(BaseModel):
    team_id: str
    user_id: str
    role: TeamRole
    joined_at: datetime

    @classmethod
    def from_domain(cls, member: TeamMember) -> "TeamMemberResponse":
        return cls(**member.model_dump())


class TeamMemberDetailResponse(BaseModel):
    user_id: str
    role: TeamRole
    joined_at: datetime
    display_name: str = ""
    email: str = ""
    avatar_url: str = ""


class ListTeamMembersResponse(BaseModel):
    members: list[TeamMemberResponse]


class ListTeamMemberDetailsResponse(BaseModel):
    members: list[TeamMemberDetailResponse]


class CreateTeamInvitationRequest(BaseModel):
    role: TeamRole = TeamRole.MEMBER


class UpdateTeamMemberRoleRequest(BaseModel):
    role: TeamRole


class InvitationLinkResponse(BaseModel):
    url: str
