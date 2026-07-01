#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class RefreshToken(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    token_hash: str
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    user_agent: str = ""
    ip_address: str = ""
    created_at: datetime = Field(default_factory=datetime.now)

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None
