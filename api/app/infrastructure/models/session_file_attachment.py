#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, text, PrimaryKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class SessionFileAttachmentModel(Base):
    """会话附件文件（按 file_id 分行存储，避免 sessions.files JSONB 数组全量读写）"""

    __tablename__ = "session_file_attachments"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "file_id", name="pk_session_file_attachments"),
    )

    session_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False, server_default=text("''"))
    filepath: Mapped[str] = mapped_column(String(1024), nullable=False, server_default=text("''"))
    key: Mapped[str] = mapped_column(String(1024), nullable=False, server_default=text("''"))
    extension: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"))
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"))
    size: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )
