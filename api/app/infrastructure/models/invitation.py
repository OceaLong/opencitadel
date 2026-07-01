#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.invitation import Invitation, InvitationType
from app.domain.models.team import TeamRole
from .base import Base


class InvitationORM(Base):
    __tablename__ = "invitations"

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    type: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'platform'"))
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    team_id: Mapped[Optional[str]] = mapped_column(String(255), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True)
    team_role: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    token: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    invited_by: Mapped[Optional[str]] = mapped_column(String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    accepted_user_id: Mapped[Optional[str]] = mapped_column(String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)"))

    @classmethod
    def from_domain(cls, invitation: Invitation) -> "InvitationORM":
        return cls(
            id=invitation.id,
            type=invitation.type.value,
            email=invitation.email,
            team_id=invitation.team_id,
            team_role=invitation.team_role.value if invitation.team_role else None,
            token=invitation.token,
            invited_by=invitation.invited_by,
            expires_at=invitation.expires_at,
            accepted_at=invitation.accepted_at,
            accepted_user_id=invitation.accepted_user_id,
            created_at=invitation.created_at,
        )

    def update_from_domain(self, invitation: Invitation) -> None:
        self.type = invitation.type.value
        self.email = invitation.email
        self.team_id = invitation.team_id
        self.team_role = invitation.team_role.value if invitation.team_role else None
        self.token = invitation.token
        self.invited_by = invitation.invited_by
        self.expires_at = invitation.expires_at
        self.accepted_at = invitation.accepted_at
        self.accepted_user_id = invitation.accepted_user_id

    def to_domain(self) -> Invitation:
        return Invitation(
            id=self.id,
            type=InvitationType(self.type),
            email=self.email,
            team_id=self.team_id,
            team_role=TeamRole(self.team_role) if self.team_role else None,
            token=self.token,
            invited_by=self.invited_by,
            expires_at=self.expires_at,
            accepted_at=self.accepted_at,
            accepted_user_id=self.accepted_user_id,
            created_at=self.created_at,
        )
