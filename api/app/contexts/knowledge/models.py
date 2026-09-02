"""Greenfield files, Artifacts, and immutable knowledge resources."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.contexts.database import GreenfieldBase as Base


class _OwnerColumns:
    owner_user_id: Mapped[str | None] = mapped_column(String(255))
    team_id: Mapped[str | None] = mapped_column(String(255))


_EXACT_OWNER = "(owner_user_id IS NOT NULL) <> (team_id IS NOT NULL)"


class FileORM(_OwnerColumns, Base):
    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint(_EXACT_OWNER, name="ck_files_exactly_one_owner"),
        Index("ix_files_scope", "owner_user_id", "team_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ArtifactORM(_OwnerColumns, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        CheckConstraint(_EXACT_OWNER, name="ck_artifacts_exactly_one_owner"),
        Index("ix_artifacts_run", "run_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("kernel_runs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_ref: Mapped[str] = mapped_column(Text, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    version_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeBaseORM(_OwnerColumns, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        CheckConstraint(_EXACT_OWNER, name="ck_knowledge_bases_exactly_one_owner"),
        ForeignKeyConstraint(
            ["active_version_id", "id"],
            ["knowledge_versions.id", "knowledge_versions.knowledge_base_id"],
            name="fk_knowledge_active_version",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("ix_knowledge_bases_scope", "owner_user_id", "team_id", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    active_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeVersionORM(Base):
    __tablename__ = "knowledge_versions"
    __table_args__ = (
        CheckConstraint(
            "state IN ('candidate', 'published', 'failed')",
            name="ck_knowledge_versions_state",
        ),
        UniqueConstraint("id", "knowledge_base_id", name="uq_knowledge_version_owner"),
        UniqueConstraint("build_run_id", name="uq_knowledge_version_build_run"),
        Index("ix_knowledge_versions_base", "knowledge_base_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    build_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("kernel_runs.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    manifest_digest: Mapped[str | None] = mapped_column(String(64))
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeDocumentORM(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("version_id", "source_digest", name="uq_knowledge_document_source"),
        Index("ix_knowledge_documents_base_version", "knowledge_base_id", "version_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeChunkORM(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal", name="uq_knowledge_chunk_ordinal"),
        Index("ix_knowledge_chunks_base_version", "knowledge_base_id", "version_id"),
        Index("ix_knowledge_chunks_tsv", "content_tsv", postgresql_using="gin"),
        Index(
            "ix_knowledge_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_tsv: Mapped[Any] = mapped_column(TSVECTOR, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)


__all__ = [name for name in globals() if name.endswith("ORM")]
