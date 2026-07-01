#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import String, Integer, DateTime, Text, text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from ...domain.models.memory_entry import MemoryEntry, MemoryScope, MemorySource

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover
    Vector = None  # type: ignore[misc, assignment]

_EMBEDDING_DIM = 1536


class MemoryEntryORM(Base):
    """长期记忆ORM"""
    __tablename__ = "memory_entries"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'global'"))
    session_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False, server_default=text("''"))
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    tags: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
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
    source: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'manual'"))
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(_EMBEDDING_DIM) if Vector is not None else JSONB,
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
