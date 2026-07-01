#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.questionnaire import Questionnaire, QuestionnaireResponse, QuestionnaireStatus
from .base import Base


class QuestionnaireModel(Base):
    __tablename__ = "questionnaires"
    __table_args__ = (
        Index("ix_questionnaires_slug", "slug", unique=True),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False, server_default=text("''"))
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    questions: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'draft'"))
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    manage_token: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_user_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(self) -> Questionnaire:
        return Questionnaire(
            id=self.id,
            title=self.title,
            description=self.description or "",
            questions=self.questions or [],
            status=QuestionnaireStatus(self.status),
            slug=self.slug,
            manage_token=self.manage_token,
            created_at=self.created_at,
            updated_at=self.updated_at,
            owner_user_id=self.owner_user_id,
        )

    @classmethod
    def from_domain(cls, q: Questionnaire) -> "QuestionnaireModel":
        return cls(
            id=q.id,
            title=q.title,
            description=q.description,
            questions=q.questions,
            status=q.status.value,
            slug=q.slug,
            manage_token=q.manage_token,
            owner_user_id=q.owner_user_id,
            created_at=q.created_at,
            updated_at=q.updated_at,
        )


class QuestionnaireResponseModel(Base):
    __tablename__ = "questionnaire_responses"
    __table_args__ = (
        Index("ix_questionnaire_responses_qid", "questionnaire_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    questionnaire_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("questionnaires.id", ondelete="CASCADE"), nullable=False
    )
    answers: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    respondent_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(self) -> QuestionnaireResponse:
        return QuestionnaireResponse(
            id=self.id,
            questionnaire_id=self.questionnaire_id,
            answers=self.answers or {},
            respondent_name=self.respondent_name,
            created_at=self.created_at,
        )

    @classmethod
    def from_domain(cls, r: QuestionnaireResponse) -> "QuestionnaireResponseModel":
        return cls(
            id=r.id,
            questionnaire_id=r.questionnaire_id,
            answers=r.answers,
            respondent_name=r.respondent_name,
            created_at=r.created_at,
        )
