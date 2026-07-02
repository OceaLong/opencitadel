#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Text, Boolean, ForeignKey, PrimaryKeyConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from ...domain.models.notification import Notification


class NotificationModel(Base):
    __tablename__ = "notifications"
    __table_args__ = (PrimaryKeyConstraint("id", name="pk_notifications_id"),)

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP(0)"))

    def to_domain(self) -> Notification:
        return Notification.model_validate({
            "id": self.id,
            "user_id": self.user_id,
            "type": self.type,
            "session_id": self.session_id,
            "artifact_id": self.artifact_id,
            "job_id": self.job_id,
            "message": self.message,
            "read": self.read,
            "created_at": self.created_at,
        })
