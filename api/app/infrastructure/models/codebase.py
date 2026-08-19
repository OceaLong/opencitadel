#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.codebase import (
    ArtifactFormat,
    ArtifactKind,
    Codebase,
    CodebaseArtifact,
    CodebaseChunk,
    CodebaseEdge,
    CodebaseFile,
    CodebaseSourceType,
    CodebaseStatus,
    CodebaseSymbol,
    EdgeKind,
    SymbolKind,
)
from app.domain.models.codebase_version import CodeEvidenceRef
from .base import Base


class CodebaseModel(Base):
    __tablename__ = "codebases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["active_version_id", "id"],
            ["codebase_versions.id", "codebase_versions.codebase_id"],
            name="fk_codebases_active_version_owner",
            ondelete="SET NULL",
            use_alter=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False, server_default=text("''"))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'files'"))
    source_ref: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))
    language_stats: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sandbox_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    workspace_path: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'/home/ubuntu/codebase'")
    )
    snapshot_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ingest_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    vector_degraded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    active_version_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    owner_user_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    team_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(self) -> Codebase:
        return Codebase(
            id=self.id,
            name=self.name,
            source_type=CodebaseSourceType(self.source_type),
            source_ref=self.source_ref or "",
            status=CodebaseStatus(self.status),
            language_stats=self.language_stats or {},
            file_count=self.file_count or 0,
            sandbox_id=self.sandbox_id,
            workspace_path=self.workspace_path or "/home/ubuntu/codebase",
            snapshot_key=self.snapshot_key,
            ingest_task_id=self.ingest_task_id,
            error=self.error,
            vector_degraded=bool(self.vector_degraded),
            active_version_id=self.active_version_id,
            owner_user_id=self.owner_user_id,
            team_id=self.team_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_domain(cls, codebase: Codebase) -> "CodebaseModel":
        return cls(
            id=codebase.id,
            name=codebase.name,
            source_type=codebase.source_type.value,
            source_ref=codebase.source_ref,
            status=codebase.status.value,
            language_stats=codebase.language_stats,
            file_count=codebase.file_count,
            sandbox_id=codebase.sandbox_id,
            workspace_path=codebase.workspace_path,
            snapshot_key=codebase.snapshot_key,
            ingest_task_id=codebase.ingest_task_id,
            error=codebase.error,
            vector_degraded=codebase.vector_degraded,
            active_version_id=codebase.active_version_id,
            owner_user_id=codebase.owner_user_id,
            team_id=codebase.team_id,
            created_at=codebase.created_at,
            updated_at=codebase.updated_at,
        )


class CodebaseFileModel(Base):
    __tablename__ = "codebase_files"
    __table_args__ = (
        Index("ix_codebase_files_codebase_path", "codebase_id", "path"),
        Index("uq_codebase_files_version_path", "version_id", "path", unique=True),
        Index("ix_codebase_files_version", "version_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    codebase_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("codebases.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("codebase_versions.id", ondelete="CASCADE"),
        nullable=True,
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"))
    size: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    sha: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"))

    def to_domain(self) -> CodebaseFile:
        return CodebaseFile(
            id=self.id,
            codebase_id=self.codebase_id,
            version_id=self.version_id,
            path=self.path,
            language=self.language or "",
            size=self.size or 0,
            sha=self.sha or "",
        )


class CodebaseSymbolModel(Base):
    __tablename__ = "codebase_symbols"
    __table_args__ = (
        Index("ix_codebase_symbols_codebase_name", "codebase_id", "name"),
        Index("ix_codebase_symbols_version_qualified_name", "version_id", "qualified_name"),
        Index("ix_codebase_symbols_version_file", "version_id", "file_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    codebase_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("codebases.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("codebase_versions.id", ondelete="CASCADE"),
        nullable=True,
    )
    file_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("codebase_files.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    qualified_name: Mapped[str] = mapped_column(
        String(1024), nullable=False, server_default=text("''")
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'function'"))
    signature: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    start_line: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    end_line: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    parent_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    parser: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'regex'")
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )

    def to_domain(self) -> CodebaseSymbol:
        return CodebaseSymbol(
            id=self.id,
            codebase_id=self.codebase_id,
            version_id=self.version_id,
            file_id=self.file_id,
            name=self.name,
            qualified_name=self.qualified_name or self.name,
            kind=SymbolKind(self.kind),
            signature=self.signature or "",
            start_line=self.start_line or 0,
            end_line=self.end_line or 0,
            parent_id=self.parent_id,
            parser=self.parser or "regex",
            confidence=float(self.confidence or 0),
        )


class CodebaseEdgeModel(Base):
    __tablename__ = "codebase_edges"
    __table_args__ = (
        Index("ix_codebase_edges_src", "codebase_id", "src_symbol_id"),
        Index("ix_codebase_edges_version_src", "version_id", "src_symbol_id"),
        Index("ix_codebase_edges_version_dst", "version_id", "dst_symbol_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    codebase_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("codebases.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("codebase_versions.id", ondelete="CASCADE"),
        nullable=True,
    )
    src_symbol_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("codebase_symbols.id", ondelete="CASCADE"), nullable=False
    )
    dst_symbol_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("codebase_symbols.id", ondelete="SET NULL"), nullable=True
    )
    callee_name: Mapped[str] = mapped_column(String(512), nullable=False, server_default=text("''"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'call'"))
    resolution: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'unresolved'")
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    evidence: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    def to_domain(self) -> CodebaseEdge:
        return CodebaseEdge(
            id=self.id,
            codebase_id=self.codebase_id,
            version_id=self.version_id,
            src_symbol_id=self.src_symbol_id,
            dst_symbol_id=self.dst_symbol_id,
            callee_name=self.callee_name or "",
            kind=EdgeKind(self.kind),
            resolution=self.resolution or "unresolved",
            confidence=float(self.confidence or 0),
            evidence=[CodeEvidenceRef(**item) for item in (self.evidence or [])],
        )


class CodebaseChunkModel(Base):
    __tablename__ = "codebase_chunks"
    __table_args__ = (
        Index("ix_codebase_chunks_codebase", "codebase_id"),
        Index("ix_codebase_chunks_version", "version_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    codebase_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("codebases.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("codebase_versions.id", ondelete="CASCADE"),
        nullable=True,
    )
    file_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("codebase_files.id", ondelete="SET NULL"), nullable=True
    )
    symbol_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("codebase_symbols.id", ondelete="SET NULL"), nullable=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    search_text: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))


class CodebaseArtifactModel(Base):
    __tablename__ = "codebase_artifacts"
    __table_args__ = (
        Index("ix_codebase_artifacts_codebase_kind", "codebase_id", "kind"),
        Index("ix_codebase_artifacts_version_kind", "version_id", "kind"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    codebase_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("codebases.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("codebase_versions.id", ondelete="CASCADE"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'mermaid'"))
    title: Mapped[str] = mapped_column(String(512), nullable=False, server_default=text("''"))
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    meta: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(self) -> CodebaseArtifact:
        return CodebaseArtifact(
            id=self.id,
            codebase_id=self.codebase_id,
            version_id=self.version_id,
            kind=ArtifactKind(self.kind),
            format=ArtifactFormat(self.format),
            title=self.title or "",
            content=self.content or "",
            meta=self.meta or {},
            created_at=self.created_at,
        )
