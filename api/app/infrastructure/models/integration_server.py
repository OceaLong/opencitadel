from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.inference import ResourceVisibility
from app.domain.models.integration_runtime import MCPTransport
from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord

from .base import Base


class MCPServerORM(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    transport: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'streamable_http'")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    args: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    url_encryption: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'plaintext'")
    )
    headers: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    headers_encryption: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'plaintext'")
    )
    env: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    env_encryption: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'plaintext'")
    )
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    tool_policies: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    team_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    visibility: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'global'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(
        self,
        url: str | None,
        headers: dict[str, Any] | None,
        env: dict[str, Any] | None,
    ) -> MCPServerRecord:
        return MCPServerRecord(
            id=self.id,
            name=self.name,
            transport=MCPTransport(self.transport),
            enabled=self.enabled,
            description=self.description,
            command=self.command,
            args=self.args,
            url=url,
            headers=headers,
            env=env,
            transport_options=self.extra or {},
            tool_policies=self.tool_policies or {},
            owner_user_id=self.owner_user_id,
            team_id=self.team_id,
            visibility=ResourceVisibility(self.visibility),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class A2AServerORM(Base):
    __tablename__ = "a2a_servers"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    tool_policies: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    team_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    visibility: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'global'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(self) -> A2AServerRecord:
        return A2AServerRecord(
            id=self.id,
            base_url=self.base_url,
            enabled=self.enabled,
            tool_policies=self.tool_policies or {},
            owner_user_id=self.owner_user_id,
            team_id=self.team_id,
            visibility=ResourceVisibility(self.visibility),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
