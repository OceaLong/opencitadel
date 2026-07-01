#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import String, Boolean, DateTime, Text, text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from ...domain.models.skill import Skill, SkillAgentParams


class SkillORM(Base):
    """Skill ORM"""
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    icon: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'🤖'"))
    category: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("'general'"))
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    allowed_tools: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    mcp_server_refs: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    a2a_server_refs: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    recommended_model_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("llm_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_params: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    examples: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    owner_user_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'global'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    @classmethod
    def from_domain(cls, skill: Skill) -> "SkillORM":
        return cls(
            id=skill.id,
            name=skill.name,
            slug=skill.slug,
            description=skill.description,
            icon=skill.icon,
            category=skill.category,
            system_prompt=skill.system_prompt,
            allowed_tools=skill.allowed_tools,
            mcp_server_refs=skill.mcp_server_refs,
            a2a_server_refs=skill.a2a_server_refs,
            recommended_model_id=skill.recommended_model_id,
            agent_params=skill.agent_params.model_dump(),
            examples=skill.examples,
            is_builtin=skill.is_builtin,
            enabled=skill.enabled,
            owner_user_id=skill.owner_user_id,
            visibility=skill.visibility.value if hasattr(skill.visibility, "value") else skill.visibility,
        )

    def to_domain(self) -> Skill:
        return Skill(
            id=self.id,
            name=self.name,
            slug=self.slug,
            description=self.description,
            icon=self.icon,
            category=self.category,
            system_prompt=self.system_prompt,
            allowed_tools=self.allowed_tools or [],
            mcp_server_refs=self.mcp_server_refs or [],
            a2a_server_refs=self.a2a_server_refs or [],
            recommended_model_id=self.recommended_model_id,
            agent_params=SkillAgentParams(**(self.agent_params or {})),
            examples=self.examples or [],
            is_builtin=self.is_builtin,
            enabled=self.enabled,
            owner_user_id=self.owner_user_id,
            visibility=self.visibility,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
