#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.domain.models.team import TeamRole


class InvitationType(str, Enum):
    PLATFORM = "platform"
    TEAM = "team"


class Invitation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: InvitationType = InvitationType.PLATFORM
    email: Optional[str] = None
    team_id: Optional[str] = None
    team_role: Optional[TeamRole] = None
    token: str
    invited_by: Optional[str] = None
    expires_at: datetime
    accepted_at: Optional[datetime] = None
    accepted_user_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)

    @property
    def accepted(self) -> bool:
        return self.accepted_at is not None
