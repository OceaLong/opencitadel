#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.audit_log import AuditLog
from .base import Base


class AuditLogORM(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_user_id: Mapped[Optional[str]] = mapped_column(String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_ip: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"))
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    team_id: Mapped[Optional[str]] = mapped_column(String(255), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)"))

    @classmethod
    def from_domain(cls, log: AuditLog) -> "AuditLogORM":
        return cls(
            id=log.id,
            actor_user_id=log.actor_user_id,
            actor_ip=log.actor_ip,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            team_id=log.team_id,
            request_id=log.request_id,
            metadata_json=log.metadata,
            created_at=log.created_at,
        )

    def to_domain(self) -> AuditLog:
        return AuditLog(
            id=self.id,
            actor_user_id=self.actor_user_id,
            actor_ip=self.actor_ip,
            action=self.action,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
            team_id=self.team_id,
            request_id=self.request_id,
            metadata=self.metadata_json or {},
            created_at=self.created_at,
        )
