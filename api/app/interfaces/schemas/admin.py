#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.domain.models.audit_log import AuditLog
from app.domain.models.user import GlobalRole, User, UserStatus


class AdminUserResponse(BaseModel):
    id: str
    email: str
    username: str
    display_name: str
    global_role: GlobalRole
    status: UserStatus
    token_version: int
    created_at: datetime
    last_login_at: Optional[datetime] = None

    @classmethod
    def from_domain(cls, user: User) -> "AdminUserResponse":
        return cls(**user.model_dump(exclude={"password_hash", "avatar_url", "updated_at"}))


class ListAdminUsersResponse(BaseModel):
    users: list[AdminUserResponse]


class PatchUserRequest(BaseModel):
    global_role: Optional[GlobalRole] = None
    status: Optional[UserStatus] = None
    display_name: Optional[str] = None


class CreatePlatformInvitationRequest(BaseModel):
    email: str


class QuotaRequest(BaseModel):
    monthly_token_limit: Optional[int] = None
    daily_session_limit: Optional[int] = None
    max_concurrent_tasks: Optional[int] = None
    max_storage_bytes: Optional[int] = None


class AuditLogResponse(BaseModel):
    id: str
    actor_user_id: Optional[str]
    action: str
    resource_type: str
    resource_id: str
    team_id: Optional[str]
    request_id: str
    created_at: datetime

    @classmethod
    def from_domain(cls, log: AuditLog) -> "AuditLogResponse":
        return cls(**log.model_dump(exclude={"actor_ip", "metadata"}))


class ListAuditLogsResponse(BaseModel):
    logs: list[AuditLogResponse]
