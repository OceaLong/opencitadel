from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.inference import PLATFORM_EMBEDDING_DIMENSIONS
from app.domain.models.knowledge_base import (
    ChunkLevel,
    DocStatus,
    KBSourceType,
    KBStatus,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEntity,
    KnowledgeEntityRef,
    KnowledgeRelation,
)

from .base import Base


class KnowledgeBaseModel(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["active_version_id", "id"],
            [
                "knowledge_base_versions.id",
                "knowledge_base_versions.knowledge_base_id",
            ],
            name="fk_knowledge_bases_active_version_owner",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        # RLS predicate shape; leading team_id also serves the teams FK scan.
        Index("ix_knowledge_bases_team_updated", "team_id", "updated_at"),
        # RLS personal scope (team_id IS NULL AND owner_user_id = :user).
        Index(
            "ix_knowledge_bases_owner_updated",
            "owner_user_id",
            "updated_at",
            postgresql_where=text("team_id IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False, server_default=text("''"))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    vector_degraded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    active_version_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    # 投影器乐观守卫：最近应用到本行执行态列的 execution_events.position。
    # 仅由 PostgresFormalProjector 写（带 last_event_position 单调守卫）；
    # 领域模型与仓储层的普通写路径不触碰该列。
    last_event_position: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,  # users FK integrity scan (partial owner index only covers team_id IS NULL rows)
    )
    team_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )  # indexed via ix_knowledge_bases_team_updated composite
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # 软删除时间戳；NULL 表示未删除。仓储层维护，不进入领域模型或 from_domain/to_domain。

    def to_domain(self) -> KnowledgeBase:
        return KnowledgeBase(
            id=self.id,
            name=self.name,
            status=KBStatus(self.status),
            doc_count=self.doc_count or 0,
            chunk_count=self.chunk_count or 0,
            error=self.error,
            vector_degraded=bool(self.vector_degraded),
            active_version_id=self.active_version_id,
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
            error=kb.error,
            vector_degraded=kb.vector_degraded,
            active_version_id=kb.active_version_id,
            settings=kb.settings,
            owner_user_id=kb.owner_user_id,
            team_id=kb.team_id,
            created_at=kb.created_at,
            updated_at=kb.updated_at,
        )


class KnowledgeDocumentModel(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "kb_id",
            name="uq_knowledge_documents_id_kb",
        ),
        Index("ix_kb_documents_kb_status", "kb_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    kb_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'upload'")
    )
    source_ref: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    mime: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
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
        ForeignKeyConstraint(
            ["version_id", "kb_id"],
            [
                "knowledge_base_versions.id",
                "knowledge_base_versions.knowledge_base_id",
            ],
            name="fk_knowledge_chunks_version_owner",
        ),
        ForeignKeyConstraint(
            ["version_id", "doc_id"],
            [
                "knowledge_base_version_documents.version_id",
                "knowledge_base_version_documents.document_id",
            ],
            name="fk_knowledge_chunks_manifest_membership",
        ),
        UniqueConstraint(
            "version_id",
            "id",
            name="uq_kb_chunks_version_id",
        ),
        Index("ix_kb_chunks_kb_level", "kb_id", "level"),
        Index("ix_kb_chunks_parent", "parent_id"),
        Index("ix_kb_chunks_doc_ordinal", "doc_id", "ordinal"),
        Index("ix_kb_chunks_version_doc", "version_id", "doc_id"),
        Index("ix_kb_chunks_version", "version_id"),
        Index(
            "ix_kb_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_kb_chunks_tsv",
            "content_tsv",
            postgresql_using="gin",
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    kb_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    doc_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), nullable=True
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'child'"))
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    content_tsv: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(PLATFORM_EMBEDDING_DIMENSIONS), nullable=True
    )
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heading_path: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    ordinal: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))

    def to_domain(self) -> KnowledgeChunk:
        return KnowledgeChunk(
            id=self.id,
            kb_id=self.kb_id,
            doc_id=self.doc_id,
            version_id=self.version_id,
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
        ForeignKeyConstraint(
            ["version_id", "kb_id"],
            [
                "knowledge_base_versions.id",
                "knowledge_base_versions.knowledge_base_id",
            ],
            name="fk_knowledge_entities_version_owner",
        ),
        UniqueConstraint(
            "version_id",
            "id",
            name="uq_kb_entities_version_id",
        ),
        Index("ix_kb_entities_name", "kb_id", "name"),
        Index("ix_kb_entities_version_name", "version_id", "normalized_name"),
        Index(
            "uq_kb_entities_version_normalized_name_type",
            "version_id",
            "normalized_name",
            "type",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    kb_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_name: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
    type: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"))
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))

    def to_domain(self) -> KnowledgeEntity:
        return KnowledgeEntity(
            id=self.id,
            kb_id=self.kb_id,
            version_id=self.version_id,
            name=self.name,
            normalized_name=self.normalized_name,
            type=self.type or "",
            description=self.description or "",
        )


class KnowledgeRelationModel(Base):
    __tablename__ = "knowledge_relations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "kb_id"],
            [
                "knowledge_base_versions.id",
                "knowledge_base_versions.knowledge_base_id",
            ],
            name="fk_knowledge_relations_version_owner",
        ),
        ForeignKeyConstraint(
            ["version_id", "src_entity_id"],
            ["knowledge_entities.version_id", "knowledge_entities.id"],
            name="fk_knowledge_relations_version_src",
        ),
        ForeignKeyConstraint(
            ["version_id", "dst_entity_id"],
            ["knowledge_entities.version_id", "knowledge_entities.id"],
            name="fk_knowledge_relations_version_dst",
        ),
        ForeignKeyConstraint(
            ["version_id", "chunk_id"],
            ["knowledge_chunks.version_id", "knowledge_chunks.id"],
            name="fk_knowledge_relations_version_chunk",
        ),
        Index("ix_kb_relations_src", "kb_id", "src_entity_id"),
        Index("ix_kb_relations_dst", "kb_id", "dst_entity_id"),
        Index("ix_kb_relations_version_src", "version_id", "src_entity_id"),
        Index("ix_kb_relations_version_dst", "version_id", "dst_entity_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    kb_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    src_entity_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,  # knowledge_entities CASCADE scan (composite indexes do not lead with src_entity_id)
    )
    dst_entity_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,  # knowledge_entities CASCADE scan (composite indexes do not lead with dst_entity_id)
    )
    relation: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    chunk_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("knowledge_chunks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,  # knowledge_chunks SET NULL FK integrity scan
    )

    def to_domain(self) -> KnowledgeRelation:
        return KnowledgeRelation(
            id=self.id,
            kb_id=self.kb_id,
            version_id=self.version_id,
            src_entity_id=self.src_entity_id,
            dst_entity_id=self.dst_entity_id,
            relation=self.relation or "",
            chunk_id=self.chunk_id,
        )


class KnowledgeEntityRefModel(Base):
    __tablename__ = "knowledge_entity_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "kb_id"],
            [
                "knowledge_base_versions.id",
                "knowledge_base_versions.knowledge_base_id",
            ],
            name="fk_knowledge_entity_refs_version_owner",
        ),
        ForeignKeyConstraint(
            ["version_id", "doc_id"],
            [
                "knowledge_base_version_documents.version_id",
                "knowledge_base_version_documents.document_id",
            ],
            name="fk_knowledge_entity_refs_manifest_membership",
        ),
        ForeignKeyConstraint(
            ["version_id", "entity_id"],
            ["knowledge_entities.version_id", "knowledge_entities.id"],
            name="fk_knowledge_entity_refs_version_entity",
        ),
        UniqueConstraint("entity_id", "doc_id", name="uq_kb_entity_refs_entity_doc"),
        Index("ix_kb_entity_refs_doc", "doc_id"),
        Index("ix_kb_entity_refs_entity", "entity_id"),
        Index("ix_kb_entity_refs_version_doc", "version_id", "doc_id"),
        Index("ix_kb_entity_refs_version_entity", "version_id", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    kb_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    entity_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False
    )
    doc_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(self) -> KnowledgeEntityRef:
        return KnowledgeEntityRef(
            id=self.id,
            kb_id=self.kb_id,
            version_id=self.version_id,
            entity_id=self.entity_id,
            doc_id=self.doc_id,
        )
