#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class AuditLog(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    actor_user_id: Optional[str] = None
    actor_ip: str = ""
    action: str
    resource_type: str = ""
    resource_id: str = ""
    team_id: Optional[str] = None
    request_id: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    chain_seq: Optional[int] = None
    prev_hash: str = ""
    entry_hash: str = ""
