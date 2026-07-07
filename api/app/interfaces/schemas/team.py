#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.domain.models.team import Team, TeamMember, TeamRole
from app.interfaces.schemas.admin import InvitationStatus


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
    email: str | None = None


class TeamInvitationRegisterRequest(BaseModel):
    email: str
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class TeamInvitationPreviewResponse(BaseModel):
    team_id: str
    team_name: str
    role: TeamRole
    status: InvitationStatus
    expires_at: datetime
    requires_registration: bool
    email_hint: str | None = None


class UpdateTeamMemberRoleRequest(BaseModel):
    role: TeamRole


class InvitationLinkResponse(BaseModel):
    url: str
