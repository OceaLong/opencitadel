#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: str
    user_id: str
    type: str
    session_id: Optional[str] = None
    artifact_id: Optional[str] = None
    job_id: Optional[str] = None
    message: str
    i18n_key: Optional[str] = None
    i18n_params: Optional[dict] = None
    read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    notifications: List[NotificationResponse]
    unread_count: int


class PendingPlanUpdateRequest(BaseModel):
    plan: dict
