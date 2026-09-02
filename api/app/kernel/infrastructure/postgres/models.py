"""Greenfield SQLAlchemy tables owned by the durable kernel."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.contexts.database import GreenfieldBase as Base


class _OwnerColumns:
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class KernelRunORM(_OwnerColumns, Base):
    __tablename__ = "kernel_runs"
    __table_args__ = (
        CheckConstraint(
            "(owner_user_id IS NOT NULL) <> (team_id IS NOT NULL)",
            name="ck_kernel_runs_exactly_one_owner",
        ),
        Index("ix_kernel_runs_personal", "owner_user_id", "created_at"),
        Index("ix_kernel_runs_team", "team_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workflow: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stream_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stream_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KernelCommandORM(_OwnerColumns, Base):
    __tablename__ = "kernel_commands"
    __table_args__ = (
        CheckConstraint(
            "(owner_user_id IS NOT NULL) <> (team_id IS NOT NULL)",
            name="ck_kernel_commands_exactly_one_owner",
        ),
        Index("ix_kernel_commands_run", "run_id", "submitted_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    workflow: Mapped[str] = mapped_column(String(40), nullable=False)
    command_type: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_stream_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KernelEventORM(_OwnerColumns, Base):
    __tablename__ = "kernel_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_kernel_events_event_id"),
        CheckConstraint(
            "(owner_user_id IS NOT NULL) <> (team_id IS NOT NULL)",
            name="ck_kernel_events_exactly_one_owner",
        ),
        Index("ix_kernel_events_scope_personal", "owner_user_id", "occurred_at"),
        Index("ix_kernel_events_scope_team", "team_id", "occurred_at"),
    )

    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("kernel_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    public_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    private_payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    causation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KernelEffectORM(_OwnerColumns, Base):
    __tablename__ = "kernel_effects"
    __table_args__ = (
        CheckConstraint(
            "(owner_user_id IS NOT NULL) <> (team_id IS NOT NULL)",
            name="ck_kernel_effects_exactly_one_owner",
        ),
        Index("ix_kernel_effects_claim", "status", "next_attempt_at"),
        Index("ix_kernel_effects_run", "run_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    invocation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True, nullable=False)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("kernel_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    effect_type: Mapped[str] = mapped_column(String(120), nullable=False)
    safety: Mapped[str] = mapped_column(String(40), nullable=False)
    request_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    public_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    approval_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claim_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claim_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KernelTimerORM(_OwnerColumns, Base):
    __tablename__ = "kernel_timers"
    __table_args__ = (
        CheckConstraint(
            "(owner_user_id IS NOT NULL) <> (team_id IS NOT NULL)",
            name="ck_kernel_timers_exactly_one_owner",
        ),
        Index("ix_kernel_timers_due", "status", "due_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("kernel_runs.id", ondelete="CASCADE"), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    command_type: Mapped[str] = mapped_column(String(120), nullable=False)
    command_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    claim_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claim_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KernelOutboxORM(Base):
    __tablename__ = "kernel_outbox"
    __table_args__ = (Index("ix_kernel_outbox_pending", "delivered_at", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    topic: Mapped[str] = mapped_column(String(80), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KernelRunViewORM(_OwnerColumns, Base):
    __tablename__ = "kernel_run_views"
    __table_args__ = (
        CheckConstraint(
            "(owner_user_id IS NOT NULL) <> (team_id IS NOT NULL)",
            name="ck_kernel_run_views_exactly_one_owner",
        ),
        Index("ix_kernel_run_views_personal", "owner_user_id", "updated_at"),
        Index("ix_kernel_run_views_team", "team_id", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workflow: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    current_turn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wait_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    stream_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KernelMessageViewORM(_OwnerColumns, Base):
    __tablename__ = "kernel_message_views"
    __table_args__ = (
        CheckConstraint(
            "(owner_user_id IS NOT NULL) <> (team_id IS NOT NULL)",
            name="ck_kernel_message_views_exactly_one_owner",
        ),
        UniqueConstraint("run_id", "event_version", name="uq_kernel_message_order"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KernelEffectViewORM(_OwnerColumns, Base):
    __tablename__ = "kernel_effect_views"
    __table_args__ = (
        CheckConstraint(
            "(owner_user_id IS NOT NULL) <> (team_id IS NOT NULL)",
            name="ck_kernel_effect_views_exactly_one_owner",
        ),
        Index("ix_kernel_effect_views_run", "run_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    effect_type: Mapped[str] = mapped_column(String(120), nullable=False)
    safety: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    approval_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    public_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KernelApprovalViewORM(_OwnerColumns, Base):
    __tablename__ = "kernel_approval_views"
    __table_args__ = (
        CheckConstraint(
            "(owner_user_id IS NOT NULL) <> (team_id IS NOT NULL)",
            name="ck_kernel_approval_views_exactly_one_owner",
        ),
        Index("ix_kernel_approval_views_status", "status", "requested_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    effect_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    risk_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(24), nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KernelApprovalReviewerORM(Base):
    __tablename__ = "kernel_approval_reviewers"

    approval_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("kernel_approval_views.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)


class KernelPublicEventORM(_OwnerColumns, Base):
    __tablename__ = "kernel_public_events"
    __table_args__ = (
        CheckConstraint(
            "(owner_user_id IS NOT NULL) <> (team_id IS NOT NULL)",
            name="ck_kernel_public_events_exactly_one_owner",
        ),
        UniqueConstraint("run_id", "event_version", name="uq_kernel_public_event_version"),
        Index("ix_kernel_public_events_run", "run_id", "position"),
    )

    position: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True, nullable=False)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KernelResourceBuildViewORM(_OwnerColumns, Base):
    __tablename__ = "kernel_resource_build_views"
    __table_args__ = (
        CheckConstraint(
            "(owner_user_id IS NOT NULL) <> (team_id IS NOT NULL)",
            name="ck_kernel_resource_build_views_exactly_one_owner",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(255), nullable=False)
    candidate_version_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    active_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    manifest_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KernelNotificationViewORM(Base):
    __tablename__ = "kernel_notification_views"
    __table_args__ = (Index("ix_kernel_notifications_user", "user_id", "read", "created_at"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


KERNEL_PROJECTION_TABLES = (
    KernelRunViewORM.__table__,
    KernelMessageViewORM.__table__,
    KernelEffectViewORM.__table__,
    KernelApprovalViewORM.__table__,
    KernelApprovalReviewerORM.__table__,
    KernelPublicEventORM.__table__,
    KernelResourceBuildViewORM.__table__,
    KernelNotificationViewORM.__table__,
)

KERNEL_TABLES = (
    KernelRunORM.__table__,
    KernelCommandORM.__table__,
    KernelEventORM.__table__,
    KernelEffectORM.__table__,
    KernelTimerORM.__table__,
    KernelOutboxORM.__table__,
    *KERNEL_PROJECTION_TABLES,
)

__all__ = [
    "KERNEL_PROJECTION_TABLES",
    "KERNEL_TABLES",
    "KernelApprovalReviewerORM",
    "KernelApprovalViewORM",
    "KernelCommandORM",
    "KernelEffectORM",
    "KernelEffectViewORM",
    "KernelEventORM",
    "KernelMessageViewORM",
    "KernelNotificationViewORM",
    "KernelOutboxORM",
    "KernelPublicEventORM",
    "KernelResourceBuildViewORM",
    "KernelRunORM",
    "KernelRunViewORM",
    "KernelTimerORM",
]
