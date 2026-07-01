#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.knowledge_base import (
    ChunkLevel,
    DocStatus,
    KBSourceType,
    KBStatus,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEntity,
    KnowledgeRelation,
)
from .base import Base


class KnowledgeBaseModel(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False, server_default=text("''"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    ingest_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    vector_degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    settings: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
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

    def to_domain(self) -> KnowledgeBase:
        return KnowledgeBase(
            id=self.id,
            name=self.name,
            status=KBStatus(self.status),
            doc_count=self.doc_count or 0,
            chunk_count=self.chunk_count or 0,
            ingest_task_id=self.ingest_task_id,
            error=self.error,
            vector_degraded=bool(self.vector_degraded),
            settings=self.settings or {},
            owner_user_id=self.owner_user_id,
            team_id=self.team_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_domain(cls, kb: KnowledgeBase) -> "KnowledgeBaseModel":
        return cls(
            id=kb.id,
            name=kb.name,
            status=kb.status.value,
            doc_count=kb.doc_count,
            chunk_count=kb.chunk_count,
            ingest_task_id=kb.ingest_task_id,
            error=kb.error,
            vector_degraded=kb.vector_degraded,
            settings=kb.settings,
            owner_user_id=kb.owner_user_id,
            team_id=kb.team_id,
            created_at=kb.created_at,
            updated_at=kb.updated_at,
        )


class KnowledgeDocumentModel(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("ix_kb_documents_kb_status", "kb_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    kb_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'upload'"))
    source_ref: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    mime: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    warning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(self) -> KnowledgeDocument:
        return KnowledgeDocument(
            id=self.id,
            kb_id=self.kb_id,
            title=self.title,
            source_type=KBSourceType(self.source_type),
            source_ref=self.source_ref or "",
            mime=self.mime or "",
            file_id=self.file_id,
            page_count=self.page_count or 0,
            status=DocStatus(self.status),
            error=self.error,
            warning=self.warning,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class KnowledgeChunkModel(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_kb_chunks_kb_level", "kb_id", "level"),
        Index("ix_kb_chunks_parent", "parent_id"),
        Index("ix_kb_chunks_doc_ordinal", "doc_id", "ordinal"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    kb_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    doc_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), nullable=True
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'child'"))
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    page_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    heading_path: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    ordinal: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))

    def to_domain(self) -> KnowledgeChunk:
        return KnowledgeChunk(
            id=self.id,
            kb_id=self.kb_id,
            doc_id=self.doc_id,
            parent_id=self.parent_id,
            level=ChunkLevel(self.level),
            content=self.content or "",
            page_no=self.page_no,
            heading_path=self.heading_path or "",
            ordinal=self.ordinal or 0,
        )


class KnowledgeEntityModel(Base):
    __tablename__ = "knowledge_entities"
    __table_args__ = (
        Index("ix_kb_entities_name", "kb_id", "name"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    kb_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    type: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"))
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))

    def to_domain(self) -> KnowledgeEntity:
        return KnowledgeEntity(
            id=self.id,
            kb_id=self.kb_id,
            name=self.name,
            type=self.type or "",
            description=self.description or "",
        )


class KnowledgeRelationModel(Base):
    __tablename__ = "knowledge_relations"
    __table_args__ = (
        Index("ix_kb_relations_src", "kb_id", "src_entity_id"),
        Index("ix_kb_relations_dst", "kb_id", "dst_entity_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    kb_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    src_entity_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False
    )
    dst_entity_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    chunk_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("knowledge_chunks.id", ondelete="SET NULL"), nullable=True
    )

    def to_domain(self) -> KnowledgeRelation:
        return KnowledgeRelation(
            id=self.id,
            kb_id=self.kb_id,
            src_entity_id=self.src_entity_id,
            dst_entity_id=self.dst_entity_id,
            relation=self.relation or "",
            chunk_id=self.chunk_id,
        )
