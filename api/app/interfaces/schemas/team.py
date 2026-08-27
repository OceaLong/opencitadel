from datetime import datetime

from pydantic import BaseModel, Field

from app.application.dto.team import (
    TeamInvitationPreview as TeamInvitationPreviewResponse,
)
from app.application.dto.team import (
    TeamMemberDetail as TeamMemberDetailResponse,
)
from app.domain.models.team import Team, TeamMember, TeamRole

__all__ = [
    "TeamInvitationPreviewResponse",
    "TeamMemberDetailResponse",
]


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


class ListTeamMemberDetailsResponse(BaseModel):
    members: list[TeamMemberDetailResponse]


class CreateTeamInvitationRequest(BaseModel):
    role: TeamRole = TeamRole.MEMBER
    email: str | None = None


class TeamInvitationRegisterRequest(BaseModel):
    email: str
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class UpdateTeamMemberRoleRequest(BaseModel):
    role: TeamRole


class InvitationLinkResponse(BaseModel):
    url: str
