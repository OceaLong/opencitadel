#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Literal, List, Optional, Dict, Any

from pydantic import BaseModel, Field


TriggerType = Literal["cron", "interval", "webhook"]
JobRunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


class NotifyChannel(BaseModel):
    type: str = "mcp"
    server_name: str = ""
    channel_arg: str = ""


class ScheduledJob(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    owner_user_id: str
    trigger_type: TriggerType = "interval"
    trigger_spec: str = ""
    prompt_template: str = ""
    skill_id: Optional[str] = None
    model_id: Optional[str] = None
    codebase_id: Optional[str] = None
    knowledge_base_id: Optional[str] = None
    notify_channels: List[NotifyChannel] = Field(default_factory=list)
    enabled: bool = True
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[str] = None
    last_run_session_id: Optional[str] = None
    webhook_token: Optional[str] = None
    webhook_secret_hash: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def notify_channels_dict(self) -> List[Dict[str, Any]]:
        return [c.model_dump() for c in self.notify_channels]
