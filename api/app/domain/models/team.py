import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.utils.time_utils import utc_now


class TeamRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class Team(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    created_by: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TeamMember(BaseModel):
    team_id: str
    user_id: str
    role: TeamRole = TeamRole.MEMBER
    joined_at: datetime = Field(default_factory=utc_now)
