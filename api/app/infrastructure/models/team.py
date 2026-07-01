#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, PrimaryKeyConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.team import Team, TeamMember, TeamRole
from .base import Base


class TeamORM(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False, server_default=text("''"))
    created_by: Mapped[Optional[str]] = mapped_column(String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)"))

    @classmethod
    def from_domain(cls, team: Team) -> "TeamORM":
        return cls(
            id=team.id,
            name=team.name,
            description=team.description,
            created_by=team.created_by,
            created_at=team.created_at,
            updated_at=team.updated_at,
        )

    def update_from_domain(self, team: Team) -> None:
        self.name = team.name
        self.description = team.description
        self.created_by = team.created_by
        self.updated_at = team.updated_at

    def to_domain(self) -> Team:
        return Team(
            id=self.id,
            name=self.name,
            description=self.description,
            created_by=self.created_by,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class TeamMemberORM(Base):
    __tablename__ = "team_members"
    __table_args__ = (PrimaryKeyConstraint("team_id", "user_id", name="pk_team_members"),)

    team_id: Mapped[str] = mapped_column(String(255), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'member'"))
    joined_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)"))

    @classmethod
    def from_domain(cls, member: TeamMember) -> "TeamMemberORM":
        return cls(
            team_id=member.team_id,
            user_id=member.user_id,
            role=member.role.value,
            joined_at=member.joined_at,
        )

    def to_domain(self) -> TeamMember:
        return TeamMember(
            team_id=self.team_id,
            user_id=self.user_id,
            role=TeamRole(self.role),
            joined_at=self.joined_at,
        )
