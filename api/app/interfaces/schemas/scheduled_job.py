from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.models.scheduled_job import ScheduledRunStatus


class NotifyChannelRequest(BaseModel):
    type: str = "mcp"
    server_id: str = ""
    channel_arg: str = ""


class CreateScheduledJobRequest(BaseModel):
    name: str
    trigger_type: Literal["cron", "interval", "webhook"] = "interval"
    trigger_spec: str = "3600"
    prompt_template: str
    skill_id: str | None = None
    model_id: str | None = None
    knowledge_base_id: str | None = None
    notify_channels: list[NotifyChannelRequest] = Field(default_factory=list)
    operator_scope: str | None = None
    operator_domains: list[str] = Field(default_factory=list)
    enabled: bool = True
    timezone: str = "UTC"


class UpdateScheduledJobRequest(BaseModel):
    name: str | None = None
    trigger_type: Literal["cron", "interval", "webhook"] | None = None
    trigger_spec: str | None = None
    prompt_template: str | None = None
    skill_id: str | None = None
    model_id: str | None = None
    knowledge_base_id: str | None = None
    notify_channels: list[NotifyChannelRequest] | None = None
    operator_scope: str | None = None
    operator_domains: list[str] | None = None
    enabled: bool | None = None
    timezone: str | None = None


class ScheduledJobResponse(BaseModel):
    id: str
    name: str
    owner_user_id: str
    team_id: str | None = None
    trigger_type: str
    trigger_spec: str
    prompt_template: str
    skill_id: str | None = None
    model_id: str | None = None
    knowledge_base_id: str | None = None
    notify_channels: list[NotifyChannelRequest] = Field(default_factory=list)
    operator_scope: str | None = None
    operator_domains: list[str] = Field(default_factory=list)
    enabled: bool
    timezone: str = "UTC"
    source_type: str = "generic"
    source_id: str | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_run_status: ScheduledRunStatus | None = None
    last_run_error: str | None = None
    last_run_session_id: str | None = None
    last_execution_run_id: UUID | None = None
    webhook_token: str | None = None


class CreateScheduledJobResponse(BaseModel):
    job: ScheduledJobResponse
    webhook_secret: str | None = None


class ScheduledJobListResponse(BaseModel):
    jobs: list[ScheduledJobResponse]


class WebhookSecretResponse(BaseModel):
    webhook_secret: str
    webhook_token: str


class RunHistoryItem(BaseModel):
    run_id: UUID
    family: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None


class RunHistoryListResponse(BaseModel):
    runs: list[RunHistoryItem]
