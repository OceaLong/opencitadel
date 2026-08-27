"""Application projections for team membership and invitations."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from app.domain.models.team import TeamRole


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"


class TeamMemberDetail(BaseModel):
    user_id: str
    role: TeamRole
    joined_at: datetime
    display_name: str = ""
    email: str = ""
    avatar_url: str = ""


class TeamInvitationPreview(BaseModel):
    team_id: str
    team_name: str
    role: TeamRole
    status: InvitationStatus
    expires_at: datetime
    requires_registration: bool
    email_hint: str | None = None


__all__ = ["InvitationStatus", "TeamInvitationPreview", "TeamMemberDetail"]
