#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class GlobalRole(str, Enum):
    ADMIN = "admin"
    USER = "user"


class UserStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class User(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: str
    username: str
    password_hash: Optional[str] = None
    display_name: str = ""
    avatar_url: str = ""
    global_role: GlobalRole = GlobalRole.USER
    status: UserStatus = UserStatus.ACTIVE
    token_version: int = 0
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    last_login_at: Optional[datetime] = None

    @property
    def is_admin(self) -> bool:
        return self.global_role == GlobalRole.ADMIN

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE
