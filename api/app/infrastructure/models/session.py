#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String,
    Integer,
    Boolean,
    DateTime,
    Text,
    text,
    PrimaryKeyConstraint,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from ...domain.models.session import Session


class SessionModel(Base):
    """会话ORM模型"""
    __tablename__ = "sessions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_sessions_id"),
    )

    id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )  # 会话id
    sandbox_id: Mapped[str] = mapped_column(String(255), nullable=True)  # 沙箱id
    task_id: Mapped[str] = mapped_column(String(255), nullable=True)  # 任务id
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        server_default=text("''::character varying"),
    )  # 会话标题
    unread_message_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )  # 未读消息数
    latest_message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("''::text"),
    )  # 最后一条消息
    latest_message_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=True,
    )  # 最后一条消息时间
    model_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("llm_models.id", ondelete="SET NULL"),
        nullable=True,
    )  # 会话级模型
    skill_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("skills.id", ondelete="SET NULL"),
        nullable=True,
    )  # 会话级Skill
    thinking_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )  # 会话级思考模式
    codebase_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("codebases.id", ondelete="SET NULL"),
        nullable=True,
    )  # 关联代码库
    knowledge_base_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("knowledge_bases.id", ondelete="SET NULL"),
        nullable=True,
    )  # 关联文档知识库
    owner_user_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )  # 所属用户
    team_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )  # 所属团队
    mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'agent'"),
    )  # ask/agent
    pending_phase: Mapped[Optional[str]] = mapped_column(
        String(32),
        nullable=True,
    )  # 等待恢复的内部阶段
    pending_metadata: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )  # 门控状态细节
    operator_scope: Mapped[Optional[str]] = mapped_column(
        String(32),
        nullable=True,
    )  # Web Operator 目标系统归属
    operator_domains: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )  # 域名白名单
    gate_profile: Mapped[Optional[str]] = mapped_column(
        String(16),
        nullable=True,
    )  # loose | standard | strict
    status: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        server_default=text("''::character varying"),
    )  # 会话状态
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )  # 更新时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )  # 创建时间

    @classmethod
    def from_domain(cls, session: Session) -> "SessionModel":
        """从会话领域模型构建ORM模型"""
        return cls(
            **session.model_dump(
                mode="python",
                exclude={"memories", "files", "events", "updated_at", "created_at"},
            ),
        )

    def to_domain(self) -> Session:
        """将会话ORM模型转换成领域模型"""
        return Session.model_validate({
            "id": self.id,
            "sandbox_id": self.sandbox_id,
            "task_id": self.task_id,
            "title": self.title,
            "unread_message_count": self.unread_message_count,
            "latest_message": self.latest_message,
            "latest_message_at": self.latest_message_at,
            "events": [],
            "files": [],
            "memories": {},
            "model_id": self.model_id,
            "skill_id": self.skill_id,
            "thinking_enabled": self.thinking_enabled,
            "codebase_id": self.codebase_id,
            "knowledge_base_id": self.knowledge_base_id,
            "owner_user_id": self.owner_user_id,
            "team_id": self.team_id,
            "mode": self.mode,
            "pending_phase": self.pending_phase,
            "pending_metadata": self.pending_metadata,
            "operator_scope": self.operator_scope,
            "operator_domains": self.operator_domains or [],
            "gate_profile": self.gate_profile,
            "status": self.status,
            "updated_at": self.updated_at,
            "created_at": self.created_at,
        })

    def update_from_domain(self, session: Session) -> None:
        """从传递的领域模型更新ORM数据"""
        # 1.基础字段: Python模式
        base_data = session.model_dump(
            mode="python",
            exclude={"memories", "files", "events", "updated_at", "created_at"},
        )

        for field, value in base_data.items():
            setattr(self, field, value)
