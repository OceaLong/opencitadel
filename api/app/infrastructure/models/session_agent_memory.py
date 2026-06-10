#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import DateTime, ForeignKey, String, text, PrimaryKeyConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class SessionAgentMemoryModel(Base):
    """会话 Agent 记忆（按 agent_name 分行存储，避免 sessions.memories JSONB 全量 merge）"""

    __tablename__ = "session_agent_memories"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "agent_name", name="pk_session_agent_memories"),
    )

    session_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    memory_data: Mapped[Dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{\"messages\": []}'::jsonb"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )
