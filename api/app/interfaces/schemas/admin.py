#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel

from app.domain.models.audit_log import AuditLog
from app.domain.models.invitation import Invitation
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
    total: int


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
    total: int


class UsageSummaryResponse(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int
    call_count: int


class UsageTimeseriesPoint(BaseModel):
    date: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int
    call_count: int


class UsageTimeseriesResponse(BaseModel):
    points: list[UsageTimeseriesPoint]


class UsageBreakdownItem(BaseModel):
    key: str
    total_tokens: int
    call_count: int


class UsageBreakdownResponse(BaseModel):
    dimension: str
    items: list[UsageBreakdownItem]


class AuditSummaryDayItem(BaseModel):
    date: str
    count: int


class AuditSummaryActionItem(BaseModel):
    action: str
    count: int


class AuditSummaryResponse(BaseModel):
    by_day: list[AuditSummaryDayItem]
    by_action: list[AuditSummaryActionItem]


class InvitationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"


class PlatformInvitationResponse(BaseModel):
    id: str
    email: Optional[str]
    status: InvitationStatus
    invited_by: Optional[str]
    expires_at: datetime
    accepted_at: Optional[datetime] = None
    accepted_user_id: Optional[str] = None
    created_at: datetime

    @classmethod
    def from_domain(cls, invitation: Invitation, *, now: datetime) -> "PlatformInvitationResponse":
        if invitation.accepted_at is not None:
            status = InvitationStatus.ACCEPTED
        elif invitation.expires_at < now:
            status = InvitationStatus.EXPIRED
        else:
            status = InvitationStatus.PENDING
        return cls(
            id=invitation.id,
            email=invitation.email,
            status=status,
            invited_by=invitation.invited_by,
            expires_at=invitation.expires_at,
            accepted_at=invitation.accepted_at,
            accepted_user_id=invitation.accepted_user_id,
            created_at=invitation.created_at,
        )


class ListPlatformInvitationsResponse(BaseModel):
    invitations: list[PlatformInvitationResponse]
    total: int


class AdminOverviewResponse(BaseModel):
    total_users: int
    active_users: int
    disabled_users: int
    admin_users: int
    pending_invitations: int
    accepted_invitations: int
    expired_invitations: int
