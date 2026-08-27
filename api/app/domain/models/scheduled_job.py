import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.models.operator import normalize_operator_domains
from app.domain.utils.time_utils import utc_now

TriggerType = Literal["cron", "interval", "webhook"]
ScheduledJobSourceType = Literal["generic", "patrol_pack"]


class ScheduledRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NotifyChannel(BaseModel):
    type: str = "mcp"
    server_id: str = ""
    channel_arg: str = ""


class ScheduledJob(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    owner_user_id: str
    team_id: str | None = None
    trigger_type: TriggerType = "interval"
    trigger_spec: str = ""
    prompt_template: str = ""
    skill_id: str | None = None
    model_id: str | None = None
    codebase_id: str | None = None
    knowledge_base_id: str | None = None
    notify_channels: list[NotifyChannel] = Field(default_factory=list)
    operator_scope: Literal["owned", "third_party_saas"] | None = None
    operator_domains: list[str] = Field(default_factory=list)
    enabled: bool = True
    timezone: str = "UTC"
    source_type: ScheduledJobSourceType = "generic"
    source_id: str | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_run_status: ScheduledRunStatus | None = None
    last_run_session_id: str | None = None
    last_execution_run_id: UUID | None = None
    last_run_error: str | None = None
    webhook_token: str | None = None
    webhook_secret_hash: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA name") from exc
        return value

    @field_validator("operator_domains")
    @classmethod
    def validate_operator_domains(cls, value: list[str]) -> list[str]:
        return normalize_operator_domains(value)

    @model_validator(mode="after")
    def validate_source_binding(self) -> "ScheduledJob":
        if self.source_type == "patrol_pack" and not self.source_id:
            raise ValueError("patrol_pack scheduled jobs require source_id")
        if self.source_type == "generic" and self.source_id is not None:
            raise ValueError("generic scheduled jobs cannot carry source_id")
        if self.operator_scope is not None and not self.operator_domains:
            raise ValueError("operator jobs require at least one allowed domain")
        return self

    def notify_channels_dict(self) -> list[dict[str, Any]]:
        return [c.model_dump() for c in self.notify_channels]
