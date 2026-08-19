#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.codebase_version import (
    CodebaseVersion,
    CodebaseVersionState,
)

from .base import Base


class _GCParentForeignKeyConstraint(ForeignKeyConstraint):
    """Render PG16's column-specific SET NULL for version parent cleanup."""


@compiles(_GCParentForeignKeyConstraint, "postgresql")
def _compile_gc_parent_foreign_key(
    constraint,
    compiler,
    **kwargs,
):
    rendered = compiler.visit_foreign_key_constraint(
        constraint,
        **kwargs,
    )
    return f"{rendered} ON DELETE SET NULL (parent_version_id)"


class CodebaseVersionORM(Base):
    __tablename__ = "codebase_versions"
    __table_args__ = (
        UniqueConstraint("id", "codebase_id", name="uq_codebase_versions_id_owner"),
        _GCParentForeignKeyConstraint(
            ["parent_version_id", "codebase_id"],
            ["codebase_versions.id", "codebase_versions.codebase_id"],
            name="fk_codebase_versions_parent_owner",
        ),
        Index("ix_codebase_versions_codebase_state", "codebase_id", "state"),
        Index("ix_codebase_versions_build", "build_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    codebase_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("codebases.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_version_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    build_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("resource_builds.id", ondelete="SET NULL"),
        nullable=True,
    )
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'building'"),
    )
    source_snapshot_key: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    source_revision: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("''"),
    )
    source_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("''"),
    )
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    degraded_reasons: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_domain(self) -> CodebaseVersion:
        return CodebaseVersion(
            id=self.id,
            codebase_id=self.codebase_id,
            parent_version_id=self.parent_version_id,
            build_id=self.build_id,
            state=CodebaseVersionState(self.state),
            source_snapshot_key=self.source_snapshot_key,
            source_revision=self.source_revision or "",
            source_digest=self.source_digest or "",
            capabilities=dict(self.capabilities or {}),
            degraded_reasons=list(self.degraded_reasons or []),
            metrics=dict(self.metrics or {}),
            created_at=self.created_at,
            published_at=self.published_at,
        )

    @classmethod
    def from_domain(cls, version: CodebaseVersion) -> "CodebaseVersionORM":
        return cls(
            id=version.id,
            codebase_id=version.codebase_id,
            parent_version_id=version.parent_version_id,
            build_id=version.build_id,
            state=version.state.value,
            source_snapshot_key=version.source_snapshot_key,
            source_revision=version.source_revision,
            source_digest=version.source_digest,
            capabilities=version.capabilities,
            degraded_reasons=version.degraded_reasons,
            metrics=version.metrics,
            created_at=version.created_at,
            published_at=version.published_at,
        )
