import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.models.team import TeamRole
from app.domain.utils.time_utils import utc_now


class InvitationType(StrEnum):
    PLATFORM = "platform"
    TEAM = "team"


class Invitation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: InvitationType = InvitationType.PLATFORM
    email: str | None = None
    team_id: str | None = None
    team_role: TeamRole | None = None
    token: str
    invited_by: str | None = None
    expires_at: datetime
    accepted_at: datetime | None = None
    accepted_user_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def accepted(self) -> bool:
        return self.accepted_at is not None
