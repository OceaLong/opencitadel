#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class NotifyChannelRequest(BaseModel):
    type: str = "mcp"
    server_name: str = ""
    channel_arg: str = ""


class CreateScheduledJobRequest(BaseModel):
    name: str
    trigger_type: Literal["cron", "interval", "webhook"] = "interval"
    trigger_spec: str = "3600"
    prompt_template: str
    skill_id: Optional[str] = None
    model_id: Optional[str] = None
    codebase_id: Optional[str] = None
    knowledge_base_id: Optional[str] = None
    notify_channels: List[NotifyChannelRequest] = Field(default_factory=list)
    enabled: bool = True


class ScheduledJobResponse(BaseModel):
    id: str
    name: str
    owner_user_id: str
    trigger_type: str
    trigger_spec: str
    prompt_template: str
    skill_id: Optional[str] = None
    model_id: Optional[str] = None
    codebase_id: Optional[str] = None
    knowledge_base_id: Optional[str] = None
    notify_channels: List[NotifyChannelRequest] = Field(default_factory=list)
    enabled: bool
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[str] = None
    last_run_session_id: Optional[str] = None
    webhook_token: Optional[str] = None


class CreateScheduledJobResponse(BaseModel):
    job: ScheduledJobResponse
    webhook_secret: Optional[str] = None


class ScheduledJobListResponse(BaseModel):
    jobs: List[ScheduledJobResponse]


class WebhookSecretResponse(BaseModel):
    webhook_secret: str
    webhook_token: str
