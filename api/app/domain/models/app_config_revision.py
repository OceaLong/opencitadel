#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class AppConfigRevision(BaseModel):
    id: str
    config_id: str
    scope: str
    owner_user_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    changed_by: Optional[str] = None
    note: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
