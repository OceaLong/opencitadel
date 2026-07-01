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


class ListTeamMembersResponse(BaseModel):
    members: list[TeamMemberResponse]


class CreateTeamInvitationRequest(BaseModel):
    role: TeamRole = TeamRole.MEMBER


class InvitationLinkResponse(BaseModel):
    url: str
