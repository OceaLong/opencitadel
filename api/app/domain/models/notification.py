#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


NotificationType = Literal[
    "job_complete",
    "job_failed",
    "gate_waiting",
    "artifact_final",
]


class Notification(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    type: NotificationType
    session_id: Optional[str] = None
    artifact_id: Optional[str] = None
    job_id: Optional[str] = None
    message: str = ""
    read: bool = False
    created_at: datetime = Field(default_factory=datetime.now)
