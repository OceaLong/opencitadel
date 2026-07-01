#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ServiceApiKey(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    owner_user_id: str
    name: str
    key_hash: str
    prefix: str
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None
