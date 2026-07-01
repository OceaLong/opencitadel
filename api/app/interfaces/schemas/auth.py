#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.domain.models.user import GlobalRole, User, UserStatus


class RegisterRequest(BaseModel):
    invite_token: str
    email: str
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email_or_username: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    display_name: str
    avatar_url: str
    global_role: GlobalRole
    status: UserStatus
    created_at: datetime
    last_login_at: Optional[datetime] = None

    @classmethod
    def from_domain(cls, user: User) -> "UserResponse":
        return cls(
            id=user.id,
            email=user.email,
            username=user.username,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            global_role=user.global_role,
            status=user.status,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )
