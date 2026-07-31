#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SQLAlchemy persistence models for immutable knowledge-base versions."""
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeBaseVersion,
    KnowledgeDocumentRevision,
    KnowledgeVersionDocument,
    KnowledgeVersionState,
    mutable_json_value,
)

from .base import Base


class _GCParentForeignKeyConstraint(ForeignKeyConstraint):
    """Render PG16's column-specific SET NULL without breaking SQLite QA."""


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
    action = " ON DELETE SET NULL (parent_version_id)"
    marker = " DEFERRABLE"
    if marker in rendered:
        return rendered.replace(marker, f"{action}{marker}", 1)
    return f"{rendered}{action}"


class KnowledgeBaseVersionORM(Base):
    __tablename__ = "knowledge_base_versions"
    __table_args__ = (
        CheckConstraint(
            "state IN ('building', 'ready', 'degraded', 'failed')",
            name="ck_knowledge_base_versions_state",
        ),
        UniqueConstraint(
            "id",
            "knowledge_base_id",
            name="uq_knowledge_base_versions_id_kb",
        ),
        _GCParentForeignKeyConstraint(
            ["parent_version_id", "knowledge_base_id"],
            [
                "knowledge_base_versions.id",
                "knowledge_base_versions.knowledge_base_id",
            ],
            name="fk_knowledge_base_versions_parent_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "ix_knowledge_base_versions_kb_created",
            "knowledge_base_id",
            "created_at",
        ),
        Index(
            "ix_knowledge_base_versions_kb_published",
            "knowledge_base_id",
            "published_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_version_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    build_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("resource_builds.id", ondelete="SET NULL"),
        nullable=True,
    )
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=KnowledgeVersionState.BUILDING.value,
    )
    capabilities: Mapped[dict[str, bool]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    degraded_reasons: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    legacy_snapshot: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def to_domain(self) -> KnowledgeBaseVersion:
        return KnowledgeBaseVersion(
            id=self.id,
            knowledge_base_id=self.knowledge_base_id,
            parent_version_id=self.parent_version_id,
            build_id=self.build_id,
            state=KnowledgeVersionState(self.state),
            capabilities=dict(self.capabilities or {}),
            degraded_reasons=list(self.degraded_reasons or []),
            metrics=dict(self.metrics or {}),
            legacy_snapshot=self.legacy_snapshot,
            created_at=self.created_at,
            published_at=self.published_at,
        )

    @classmethod
    def from_domain(
        cls,
        version: KnowledgeBaseVersion,
    ) -> "KnowledgeBaseVersionORM":
        return cls(
            id=version.id,
            knowledge_base_id=version.knowledge_base_id,
            parent_version_id=version.parent_version_id,
            build_id=version.build_id,
            state=version.state.value,
            capabilities=mutable_json_value(version.capabilities),
            degraded_reasons=mutable_json_value(version.degraded_reasons),
            metrics=mutable_json_value(version.metrics),
            legacy_snapshot=version.legacy_snapshot,
            created_at=version.created_at,
            published_at=version.published_at,
        )


class KnowledgeDocumentRevisionORM(Base):
    __tablename__ = "knowledge_document_revisions"
    __table_args__ = (
        CheckConstraint(
            "state IN "
            "('uploaded', 'parsing', 'parsed', 'indexing', 'indexed', 'failed')",
            name="ck_knowledge_document_revisions_state",
        ),
        UniqueConstraint(
            "id",
            "document_id",
            name="uq_knowledge_document_revisions_id_document",
        ),
        UniqueConstraint(
            "document_id",
            "source_digest",
            name="uq_knowledge_document_revisions_document_digest",
        ),
        Index(
            "ix_knowledge_document_revisions_document_created",
            "document_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("knowledge_documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_ref: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("''"),
    )
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    parsed_blocks: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    page_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=DocumentRevisionState.UPLOADED.value,
    )
    needs_chunk_clone: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    warning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def to_domain(self) -> KnowledgeDocumentRevision:
        return KnowledgeDocumentRevision(
            id=self.id,
            document_id=self.document_id,
            source_ref=self.source_ref,
            source_digest=self.source_digest,
            parsed_blocks=list(self.parsed_blocks or []),
            page_count=self.page_count,
            state=DocumentRevisionState(self.state),
            needs_chunk_clone=self.needs_chunk_clone,
            error=self.error,
            warning=self.warning,
            created_at=self.created_at,
        )

    @classmethod
    def from_domain(
        cls,
        revision: KnowledgeDocumentRevision,
    ) -> "KnowledgeDocumentRevisionORM":
        return cls(
            id=revision.id,
            document_id=revision.document_id,
            source_ref=revision.source_ref,
            source_digest=revision.source_digest,
            parsed_blocks=mutable_json_value(revision.parsed_blocks),
            page_count=revision.page_count,
            state=revision.state.value,
            needs_chunk_clone=revision.needs_chunk_clone,
            error=revision.error,
            warning=revision.warning,
            created_at=revision.created_at,
        )


class KnowledgeVersionDocumentORM(Base):
    __tablename__ = "knowledge_base_version_documents"
    __table_args__ = (
        CheckConstraint(
            "state IN "
            "('uploaded', 'parsing', 'parsed', 'indexing', 'indexed', 'failed')",
            name="ck_knowledge_base_version_documents_state",
        ),
        ForeignKeyConstraint(
            ["version_id", "knowledge_base_id"],
            [
                "knowledge_base_versions.id",
                "knowledge_base_versions.knowledge_base_id",
            ],
            name="fk_kb_version_documents_version_owner",
        ),
        ForeignKeyConstraint(
            ["document_id", "knowledge_base_id"],
            ["knowledge_documents.id", "knowledge_documents.kb_id"],
            name="fk_kb_version_documents_document_owner",
        ),
        ForeignKeyConstraint(
            ["document_revision_id", "document_id"],
            [
                "knowledge_document_revisions.id",
                "knowledge_document_revisions.document_id",
            ],
            name="fk_kb_version_documents_revision_document",
        ),
        UniqueConstraint(
            "version_id",
            "document_id",
            name="uq_kb_version_documents_version_document",
        ),
        UniqueConstraint(
            "version_id",
            "ordinal",
            name="uq_kb_version_documents_version_ordinal",
        ),
        Index(
            "ix_kb_version_documents_revision",
            "document_revision_id",
        ),
    )

    version_id: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    document_id: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
    )
    document_revision_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=DocumentRevisionState.UPLOADED.value,
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    warning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def to_domain(self) -> KnowledgeVersionDocument:
        return KnowledgeVersionDocument(
            version_id=self.version_id,
            document_id=self.document_id,
            document_revision_id=self.document_revision_id,
            ordinal=self.ordinal,
            state=DocumentRevisionState(self.state),
            error=self.error,
            warning=self.warning,
        )

    @classmethod
    def from_domain(
        cls,
        document: KnowledgeVersionDocument,
        *,
        knowledge_base_id: str,
    ) -> "KnowledgeVersionDocumentORM":
        return cls(
            version_id=document.version_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document.document_id,
            document_revision_id=document.document_revision_id,
            ordinal=document.ordinal,
            state=document.state.value,
            error=document.error,
            warning=document.warning,
        )
