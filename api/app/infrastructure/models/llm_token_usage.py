#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from app.domain.models.llm_token_usage import LLMTokenUsage


class LLMTokenUsageORM(Base):
    """LLM token 使用记录 ORM。"""
    __tablename__ = "llm_token_usages"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"))
    step: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    model_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("llm_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    call_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'stream'"))
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    @classmethod
    def from_domain(cls, usage: LLMTokenUsage) -> "LLMTokenUsageORM":
        return cls(
            id=usage.id,
            session_id=usage.session_id,
            agent=usage.agent,
            step=usage.step,
            model_id=usage.model_id,
            model_name=usage.model_name,
            call_type=usage.call_type,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )

    def to_domain(self) -> LLMTokenUsage:
        return LLMTokenUsage.model_validate(self, from_attributes=True)
