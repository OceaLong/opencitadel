from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.models.inference import PLATFORM_EMBEDDING_DIMENSIONS
from ...domain.models.memory_entry import MemoryEntry, MemoryScope, MemorySource
from .base import Base

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover
    Vector = None  # type: ignore[misc, assignment]


class MemoryEntryORM(Base):
    """长期记忆ORM"""

    __tablename__ = "memory_entries"
    __table_args__ = (
        Index(
            "ix_memory_entries_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        # RLS predicate shape; leading team_id also serves the teams FK scan.
        Index("ix_memory_entries_team_created", "team_id", "created_at"),
        # RLS personal scope (team_id IS NULL AND owner_user_id = :user).
        Index(
            "ix_memory_entries_owner_created",
            "owner_user_id",
            "created_at",
            postgresql_where=text("team_id IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'global'"))
    session_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,  # sessions FK CASCADE scan + session-scoped memory lookups
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False, server_default=text("''"))
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
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
    )  # indexed via ix_memory_entries_team_created composite
    source: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'manual'"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(PLATFORM_EMBEDDING_DIMENSIONS) if Vector is not None else JSONB,
        nullable=True,
    )

    @classmethod
    def from_domain(cls, entry: MemoryEntry) -> "MemoryEntryORM":
        return cls(
            id=entry.id,
            scope=entry.scope.value,
            session_id=entry.session_id,
            title=entry.title,
            content=entry.content,
            tags=entry.tags,
            owner_user_id=entry.owner_user_id,
            team_id=entry.team_id,
            source=entry.source.value,
            last_used_at=entry.last_used_at,
            use_count=entry.use_count,
        )

    def to_domain(self) -> MemoryEntry:
        return MemoryEntry(
            id=self.id,
            scope=MemoryScope(self.scope),
            session_id=self.session_id,
            title=self.title,
            content=self.content,
            tags=self.tags or [],
            owner_user_id=self.owner_user_id,
            team_id=self.team_id,
            source=MemorySource(self.source),
            last_used_at=self.last_used_at,
            use_count=self.use_count,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
