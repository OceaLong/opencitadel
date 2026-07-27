#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class LLMModelPreferenceORM(Base):
    __tablename__ = "llm_model_preferences"

    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    team_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    model_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("llm_models.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )

    __table_args__ = (
        CheckConstraint(
            """
            (scope_type = 'global' AND owner_user_id IS NULL AND team_id IS NULL)
            OR (scope_type = 'user' AND owner_user_id IS NOT NULL AND team_id IS NULL)
            OR (scope_type = 'team' AND owner_user_id IS NULL AND team_id IS NOT NULL)
            """,
            name="ck_llm_model_preferences_scope_owner",
        ),
    )
