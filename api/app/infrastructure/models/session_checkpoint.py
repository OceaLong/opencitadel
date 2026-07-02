#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.checkpoint import (
    Checkpoint,
    CheckpointAnchorType,
    SessionStateSnapshot,
)
from .base import Base


class SessionCheckpointModel(Base):
    """Persisted session checkpoint for per-step rollback."""

    __tablename__ = "session_checkpoints"
    __table_args__ = (
        Index("ix_session_checkpoints_session_created", "session_id", "created_at"),
        Index("ix_session_checkpoints_anchor_event", "session_id", "anchor_event_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    anchor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    anchor_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    seq_before: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    memories_snapshot: Mapped[Dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    files_snapshot: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    session_state: Mapped[Dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    sandbox_snapshot_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    browser_snapshot_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )

    def to_domain(self) -> Checkpoint:
        return Checkpoint(
            id=self.id,
            session_id=self.session_id,
            anchor_type=CheckpointAnchorType(self.anchor_type),
            anchor_event_id=self.anchor_event_id,
            label=self.label or "",
            seq_before=self.seq_before,
            memories_snapshot=self.memories_snapshot or {},
            files_snapshot=self.files_snapshot or [],
            session_state=SessionStateSnapshot.model_validate(self.session_state or {}),
            sandbox_snapshot_key=self.sandbox_snapshot_key,
            browser_snapshot_key=self.browser_snapshot_key,
            created_at=self.created_at,
        )

    @classmethod
    def from_domain(cls, checkpoint: Checkpoint) -> "SessionCheckpointModel":
        return cls(
            id=checkpoint.id,
            session_id=checkpoint.session_id,
            anchor_type=checkpoint.anchor_type.value,
            anchor_event_id=checkpoint.anchor_event_id,
            label=checkpoint.label,
            seq_before=checkpoint.seq_before,
            memories_snapshot=checkpoint.memories_snapshot,
            files_snapshot=checkpoint.files_snapshot,
            session_state=checkpoint.session_state.model_dump(mode="json"),
            sandbox_snapshot_key=checkpoint.sandbox_snapshot_key,
            browser_snapshot_key=checkpoint.browser_snapshot_key,
            created_at=checkpoint.created_at,
        )
