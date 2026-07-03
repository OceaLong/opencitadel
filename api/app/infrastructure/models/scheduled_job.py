#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, Text, Boolean, ForeignKey, PrimaryKeyConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from ...domain.models.scheduled_job import ScheduledJob, NotifyChannel


class ScheduledJobModel(Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (PrimaryKeyConstraint("id", name="pk_scheduled_jobs_id"),)

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id", ondelete="CASCADE"))
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_spec: Mapped[str] = mapped_column(String(512), nullable=False, server_default="")
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    skill_id: Mapped[Optional[str]] = mapped_column(String(255), ForeignKey("skills.id", ondelete="SET NULL"))
    model_id: Mapped[Optional[str]] = mapped_column(String(255), ForeignKey("llm_models.id", ondelete="SET NULL"))
    codebase_id: Mapped[Optional[str]] = mapped_column(String(255), ForeignKey("codebases.id", ondelete="SET NULL"))
    knowledge_base_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("knowledge_bases.id", ondelete="SET NULL"),
    )
    notify_channels: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    operator_scope: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    operator_domains: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    gate_profile: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_run_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_run_session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_run_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    webhook_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    webhook_secret_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP(0)"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP(0)"))

    def to_domain(self) -> ScheduledJob:
        channels = [NotifyChannel.model_validate(c) for c in (self.notify_channels or [])]
        return ScheduledJob.model_validate({
            "id": self.id,
            "name": self.name,
            "owner_user_id": self.owner_user_id,
            "trigger_type": self.trigger_type,
            "trigger_spec": self.trigger_spec,
            "prompt_template": self.prompt_template,
            "skill_id": self.skill_id,
            "model_id": self.model_id,
            "codebase_id": self.codebase_id,
            "knowledge_base_id": self.knowledge_base_id,
            "notify_channels": channels,
            "operator_scope": self.operator_scope,
            "operator_domains": self.operator_domains or [],
            "gate_profile": self.gate_profile,
            "enabled": self.enabled,
            "next_run_at": self.next_run_at,
            "last_run_at": self.last_run_at,
            "last_run_status": self.last_run_status,
            "last_run_session_id": self.last_run_session_id,
            "last_run_error": self.last_run_error,
            "webhook_token": self.webhook_token,
            "webhook_secret_hash": self.webhook_secret_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        })

    def update_from_domain(self, job: ScheduledJob) -> None:
        data = job.model_dump(mode="python", exclude={"created_at", "notify_channels"})
        for field, value in data.items():
            setattr(self, field, value)
        self.notify_channels = job.notify_channels_dict()
