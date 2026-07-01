#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UserQuota(BaseModel):
    user_id: str
    monthly_token_limit: Optional[int] = Field(default=None, ge=0)
    daily_session_limit: Optional[int] = Field(default=None, ge=0)
    max_concurrent_tasks: Optional[int] = Field(default=None, ge=0)
    max_storage_bytes: Optional[int] = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
