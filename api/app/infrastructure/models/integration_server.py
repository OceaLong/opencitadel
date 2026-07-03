#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import String, Boolean, DateTime, Text, text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from app.domain.models.app_config import MCPServerConfig, MCPTransport
from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord
from app.domain.models.llm_model import ResourceVisibility
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption


class MCPServerORM(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    transport: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'streamable_http'"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    command: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    args: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    url_encryption: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'legacy_plaintext'")
    )
    headers: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    headers_encryption: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'legacy_plaintext'")
    )
    env: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    env_encryption: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'legacy_plaintext'")
    )
    extra: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    owner_user_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'global'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(
        self,
        url: Optional[str],
        headers: Optional[Dict[str, Any]],
        env: Optional[Dict[str, Any]],
    ) -> MCPServerRecord:
        return MCPServerRecord(
            id=self.id,
            name=self.name,
            transport=MCPTransport(self.transport),
            enabled=self.enabled,
            description=self.description,
            command=self.command,
            args=self.args,
            url=self.url,
            headers=headers,
            env=env,
            extra=self.extra or {},
            owner_user_id=self.owner_user_id,
            visibility=ResourceVisibility(self.visibility),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class A2AServerORM(Base):
    __tablename__ = "a2a_servers"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    owner_user_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'global'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(self) -> A2AServerRecord:
        return A2AServerRecord(
            id=self.id,
            base_url=self.base_url,
            enabled=self.enabled,
            owner_user_id=self.owner_user_id,
            visibility=ResourceVisibility(self.visibility),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
