"""Greenfield inference configuration, MCP, and idempotent usage rows."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.contexts.database import GreenfieldBase as Base

_VISIBILITY_OWNER = (
    "(visibility = 'global' AND owner_user_id IS NULL AND team_id IS NULL) OR "
    "(visibility = 'private' AND ((owner_user_id IS NOT NULL) <> (team_id IS NOT NULL)))"
)


class InferenceEndpointORM(Base):
    __tablename__ = "inference_endpoints"
    __table_args__ = (
        CheckConstraint(_VISIBILITY_OWNER, name="ck_inference_endpoints_owner"),
        Index("ix_inference_endpoints_owner", "owner_user_id", "team_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    credential_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    credential_encryption: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'fernet_v2'")
    )
    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(255))
    team_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InferenceModelORM(Base):
    __tablename__ = "inference_models"
    __table_args__ = (
        CheckConstraint(_VISIBILITY_OWNER, name="ck_inference_models_owner"),
        Index("ix_inference_models_owner", "owner_user_id", "team_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    endpoint_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("inference_endpoints.id", ondelete="RESTRICT"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(255))
    team_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InferenceBindingORM(Base):
    __tablename__ = "inference_bindings"
    __table_args__ = (
        CheckConstraint(
            "(scope_type = 'global' AND scope_key = 'global' AND owner_user_id IS NULL "
            "AND team_id IS NULL) OR (scope_type = 'user' AND scope_key = owner_user_id "
            "AND team_id IS NULL) OR (scope_type = 'team' AND scope_key = team_id "
            "AND owner_user_id IS NULL)",
            name="ck_inference_bindings_scope",
        ),
        UniqueConstraint("scope_type", "scope_key", "purpose", name="uq_binding_scope_purpose"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(255))
    team_id: Mapped[str | None] = mapped_column(String(255))
    model_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("inference_models.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InferenceUsageORM(Base):
    __tablename__ = "inference_usage"
    __table_args__ = (
        CheckConstraint(
            "(owner_user_id IS NOT NULL) <> (team_id IS NOT NULL)",
            name="ck_inference_usage_exactly_one_owner",
        ),
        Index("ix_inference_usage_scope", "owner_user_id", "team_id", "created_at"),
    )

    invocation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    input_tokens: Mapped[int] = mapped_column(nullable=False)
    output_tokens: Mapped[int] = mapped_column(nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(255))
    team_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MCPServerORM(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (
        CheckConstraint(_VISIBILITY_OWNER, name="ck_mcp_servers_owner"),
        Index("ix_mcp_servers_owner", "owner_user_id", "team_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    transport: Mapped[str] = mapped_column(String(40), nullable=False)
    config_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    secret_encryption: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'fernet_v2'")
    )
    capability_catalog: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(255))
    team_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [name for name in globals() if name.endswith("ORM")]
