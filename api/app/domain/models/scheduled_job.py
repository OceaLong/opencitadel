#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Literal, List, Optional, Dict, Any

from pydantic import BaseModel, Field, field_validator, model_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TriggerType = Literal["cron", "interval", "webhook"]
ScheduledJobSourceType = Literal["generic", "patrol_pack"]


class NotifyChannel(BaseModel):
    type: str = "mcp"
    server_name: str = ""
    channel_arg: str = ""


class ScheduledJob(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    owner_user_id: str
    team_id: Optional[str] = None
    trigger_type: TriggerType = "interval"
    trigger_spec: str = ""
    prompt_template: str = ""
    skill_id: Optional[str] = None
    model_id: Optional[str] = None
    codebase_id: Optional[str] = None
    knowledge_base_id: Optional[str] = None
    notify_channels: List[NotifyChannel] = Field(default_factory=list)
    operator_scope: Optional[str] = None
    operator_domains: List[str] = Field(default_factory=list)
    gate_profile: Optional[str] = None
    enabled: bool = True
    timezone: str = "UTC"
    source_type: ScheduledJobSourceType = "generic"
    source_id: Optional[str] = None
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[str] = None
    last_run_session_id: Optional[str] = None
    last_run_error: Optional[str] = None
    webhook_token: Optional[str] = None
    webhook_secret_hash: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA name") from exc
        return value

    @model_validator(mode="after")
    def validate_source_binding(self) -> "ScheduledJob":
        if self.source_type == "patrol_pack" and not self.source_id:
            raise ValueError("patrol_pack scheduled jobs require source_id")
        if self.source_type == "generic" and self.source_id is not None:
            raise ValueError("generic scheduled jobs cannot carry source_id")
        return self

    def notify_channels_dict(self) -> List[Dict[str, Any]]:
        return [c.model_dump() for c in self.notify_channels]
