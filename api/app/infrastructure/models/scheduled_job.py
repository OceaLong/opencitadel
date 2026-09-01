import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.models.scheduled_job import NotifyChannel, ScheduledJob
from .base import Base


class ScheduledJobModel(Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_scheduled_jobs_id"),
        # RLS predicate shape; leading team_id also serves the teams FK scan.
        Index("ix_scheduled_jobs_team_updated", "team_id", "updated_at"),
        # RLS personal scope (team_id IS NULL AND owner_user_id = :user).
        Index(
            "ix_scheduled_jobs_owner_updated",
            "owner_user_id",
            "updated_at",
            postgresql_where=text("team_id IS NULL"),
        ),
        # Webhook triggers are looked up by token; it must be unique when present.
        Index(
            "uq_scheduled_jobs_webhook_token",
            "webhook_token",
            unique=True,
            postgresql_where=text("webhook_token IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,  # users FK CASCADE scan (partial owner index only covers team_id IS NULL rows)
    )
    team_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )  # indexed via ix_scheduled_jobs_team_updated composite
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_spec: Mapped[str] = mapped_column(String(512), nullable=False, server_default="")
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    skill_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("skills.id", ondelete="SET NULL"), index=True
    )
    model_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("inference_models.id", ondelete="SET NULL"), index=True
    )
    knowledge_base_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("knowledge_bases.id", ondelete="SET NULL"),
        index=True,
    )
    notify_channels: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    operator_scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    operator_domains: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="generic")
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_run_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_execution_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    last_run_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    webhook_secret_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(self) -> ScheduledJob:
        channels = [NotifyChannel.model_validate(c) for c in (self.notify_channels or [])]
        return ScheduledJob.model_validate(
            {
                "id": self.id,
                "name": self.name,
                "owner_user_id": self.owner_user_id,
                "team_id": self.team_id,
                "trigger_type": self.trigger_type,
                "trigger_spec": self.trigger_spec,
                "prompt_template": self.prompt_template,
                "skill_id": self.skill_id,
                "model_id": self.model_id,
                "knowledge_base_id": self.knowledge_base_id,
                "notify_channels": channels,
                "operator_scope": self.operator_scope,
                "operator_domains": self.operator_domains or [],
                "enabled": self.enabled,
                "timezone": self.timezone,
                "source_type": self.source_type,
                "source_id": self.source_id,
                "next_run_at": self.next_run_at,
                "last_run_at": self.last_run_at,
                "last_run_status": self.last_run_status,
                "last_run_session_id": self.last_run_session_id,
                "last_execution_run_id": self.last_execution_run_id,
                "last_run_error": self.last_run_error,
                "webhook_token": self.webhook_token,
                "webhook_secret_hash": self.webhook_secret_hash,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }
        )

    def update_from_domain(self, job: ScheduledJob) -> None:
        data = job.model_dump(mode="python", exclude={"created_at", "notify_channels"})
        for field, value in data.items():
            setattr(self, field, value)
        self.notify_channels = job.notify_channels_dict()
