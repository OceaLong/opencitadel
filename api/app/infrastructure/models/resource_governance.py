#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.models.tool_approval import (
    ApprovalStatus,
    ToolApprovalBatch,
    ToolApprovalCall,
)
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolIdempotency,
)
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
    SessionResourceBinding,
)

from .base import Base


class ToolApprovalBatchORM(Base):
    __tablename__ = "tool_approval_batches"
    __table_args__ = (
        Index(
            "ix_tool_approval_batches_pending",
            "session_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=ApprovalStatus.PENDING.value
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    calls: Mapped[list["ToolApprovalCallORM"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="ToolApprovalCallORM.ordinal",
    )

    def to_domain(self) -> ToolApprovalBatch:
        return ToolApprovalBatch(
            id=self.id,
            session_id=self.session_id,
            status=ApprovalStatus(self.status),
            expires_at=_as_utc(self.expires_at),
            created_at=_as_utc(self.created_at),
            decided_at=_as_utc(self.decided_at),
            calls=[call.to_domain() for call in self.calls],
        )

    @classmethod
    def from_domain(cls, batch: ToolApprovalBatch) -> "ToolApprovalBatchORM":
        record = cls(
            id=batch.id,
            session_id=batch.session_id,
            status=batch.status.value,
            expires_at=batch.expires_at,
            created_at=batch.created_at,
            decided_at=batch.decided_at,
        )
        record.calls = [ToolApprovalCallORM.from_domain(call) for call in batch.calls]
        return record


class ToolApprovalCallORM(Base):
    __tablename__ = "tool_approval_calls"
    __table_args__ = (
        UniqueConstraint(
            "tool_call_id", name="uq_tool_approval_calls_tool_call_id"
        ),
        UniqueConstraint(
            "batch_id", "ordinal", name="uq_tool_approval_calls_batch_ordinal"
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("tool_approval_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_args: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    args_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    effect: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency: Mapped[str] = mapped_column(String(64), nullable=False)
    approval: Mapped[str] = mapped_column(String(32), nullable=False)
    concurrency_group: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=ApprovalStatus.PENDING.value
    )
    decided_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    batch: Mapped[ToolApprovalBatchORM] = relationship(back_populates="calls")

    def to_domain(self) -> ToolApprovalCall:
        return ToolApprovalCall(
            id=self.id,
            batch_id=self.batch_id,
            tool_call_id=self.tool_call_id,
            ordinal=self.ordinal,
            tool_name=self.tool_name,
            normalized_args=self.normalized_args,
            args_hash=self.args_hash,
            capability=ToolCapability(self.capability),
            effect=ToolEffect(self.effect),
            idempotency=ToolIdempotency(self.idempotency),
            approval=ApprovalMode(self.approval),
            concurrency_group=self.concurrency_group,
            status=ApprovalStatus(self.status),
            decided_by=self.decided_by,
            decided_at=_as_utc(self.decided_at),
        )

    @classmethod
    def from_domain(cls, call: ToolApprovalCall) -> "ToolApprovalCallORM":
        return cls(
            id=call.id,
            batch_id=call.batch_id,
            tool_call_id=call.tool_call_id,
            ordinal=call.ordinal,
            tool_name=call.tool_name,
            normalized_args=call.normalized_args,
            args_hash=call.args_hash,
            capability=call.capability.value,
            effect=call.effect.value,
            idempotency=call.idempotency.value,
            approval=call.approval.value,
            concurrency_group=call.concurrency_group,
            status=call.status.value,
            decided_by=call.decided_by,
            decided_at=call.decided_at,
        )


class ResourceBuildORM(Base):
    __tablename__ = "resource_builds"
    __table_args__ = (
        Index(
            "uq_resource_builds_active",
            "resource_kind",
            "resource_id",
            unique=True,
            postgresql_where=text("state IN ('queued', 'running')"),
        ),
        Index(
            "ix_resource_builds_resource_version_state",
            "resource_kind",
            "resource_id",
            "version_id",
            "state",
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    resource_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    version_id: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_version_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    command_key: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    phase: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    progress: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    capabilities: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    degraded_reasons: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_event_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_domain(self) -> ResourceBuild:
        return ResourceBuild(
            id=self.id,
            resource_kind=ResourceKind(self.resource_kind),
            resource_id=self.resource_id,
            version_id=self.version_id,
            parent_version_id=self.parent_version_id,
            command_key=self.command_key,
            state=BuildState(self.state),
            phase=self.phase,
            progress=self.progress,
            capabilities=list(self.capabilities or []),
            degraded_reasons=list(self.degraded_reasons or []),
            metrics=dict(self.metrics or {}),
            error_code=self.error_code,
            error_message=self.error_message,
            heartbeat_at=_as_utc(self.heartbeat_at),
            last_event_seq=self.last_event_seq,
            created_by=self.created_by,
            created_at=_as_utc(self.created_at),
            started_at=_as_utc(self.started_at),
            finished_at=_as_utc(self.finished_at),
        )

    @classmethod
    def from_domain(cls, build: ResourceBuild) -> "ResourceBuildORM":
        return cls(
            id=build.id,
            resource_kind=build.resource_kind.value,
            resource_id=build.resource_id,
            version_id=build.version_id,
            parent_version_id=build.parent_version_id,
            command_key=build.command_key,
            state=build.state.value,
            phase=build.phase,
            progress=build.progress,
            capabilities=build.capabilities,
            degraded_reasons=build.degraded_reasons,
            metrics=build.metrics,
            error_code=build.error_code,
            error_message=build.error_message,
            heartbeat_at=build.heartbeat_at,
            last_event_seq=build.last_event_seq,
            created_by=build.created_by,
            created_at=build.created_at,
            started_at=build.started_at,
            finished_at=build.finished_at,
        )


class SessionResourceBindingORM(Base):
    __tablename__ = "session_resource_bindings"
    __table_args__ = (
        Index(
            "uq_session_resource_bindings_current",
            "session_id",
            "resource_kind",
            unique=True,
            postgresql_where=text("is_current = true"),
            sqlite_where=text("is_current = 1"),
        ),
        Index(
            "ix_session_resource_bindings_resource_version",
            "resource_kind",
            "resource_id",
            "version_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    resource_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    version_id: Mapped[str] = mapped_column(String(255), nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    supersedes_binding_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("session_resource_bindings.id", ondelete="SET NULL"),
        nullable=True,
    )
    bound_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    def to_domain(self) -> SessionResourceBinding:
        return SessionResourceBinding(
            id=self.id,
            session_id=self.session_id,
            resource_kind=ResourceKind(self.resource_kind),
            resource_id=self.resource_id,
            version_id=self.version_id,
            is_current=self.is_current,
            supersedes_binding_id=self.supersedes_binding_id,
            bound_by=self.bound_by,
            created_at=_as_utc(self.created_at),
        )

    @classmethod
    def from_domain(
        cls,
        binding: SessionResourceBinding,
    ) -> "SessionResourceBindingORM":
        return cls(
            id=binding.id,
            session_id=binding.session_id,
            resource_kind=binding.resource_kind.value,
            resource_id=binding.resource_id,
            version_id=binding.version_id,
            is_current=binding.is_current,
            supersedes_binding_id=binding.supersedes_binding_id,
            bound_by=binding.bound_by,
            created_at=binding.created_at,
        )


class ResourceBuildEventORM(Base):
    __tablename__ = "resource_build_events"
    __table_args__ = (
        UniqueConstraint(
            "build_id", "seq", name="uq_resource_build_events_build_seq"
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    build_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("resource_builds.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    progress: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    def to_domain(self) -> ResourceBuildEvent:
        return ResourceBuildEvent(
            id=self.id,
            build_id=self.build_id,
            seq=self.seq,
            phase=self.phase,
            state=BuildState(self.state),
            progress=self.progress,
            payload=dict(self.payload or {}),
            created_at=_as_utc(self.created_at),
        )

    @classmethod
    def from_domain(
        cls,
        event: ResourceBuildEvent,
    ) -> "ResourceBuildEventORM":
        return cls(
            id=event.id,
            build_id=event.build_id,
            seq=event.seq,
            phase=event.phase,
            state=event.state.value,
            progress=event.progress,
            payload=event.payload,
            created_at=event.created_at,
        )


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
