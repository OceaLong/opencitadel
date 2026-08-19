#!/usr/bin/env python
# -*- coding: utf-8 -*-
from fastapi import Depends, Header, Request

from app.application.security.authorization_context import (
    get_authorization_context,
    set_authorization_context,
)
from app.domain.errors import ForbiddenError, UnauthorizedError
from app.domain.models.authorization import AuthorizationContext, AuthorizationMode
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.infrastructure.security.csrf import CsrfService
from app.infrastructure.security.service_api_key import ServiceApiKeyHasher
from app.infrastructure.storage.postgres import get_uow
from app.interfaces.auth_context import get_principal
from app.interfaces.auth_context import set_principal
from app.infrastructure.observability.logging_context import get_request_id


async def get_current_principal() -> Principal:
    principal = get_principal()
    if principal is None:
        raise UnauthorizedError()
    if get_authorization_context().mode == AuthorizationMode.ANONYMOUS:
        set_authorization_context(
            AuthorizationContext.for_principal(
                principal,
                request_id=get_request_id() or "",
            )
        )
    return principal


async def require_admin() -> Principal:
    principal = await get_current_principal()
    if not principal.is_admin:
        raise ForbiddenError("需要管理员权限", error_key="errors.adminRequired")
    return principal


async def require_auditor_or_admin() -> Principal:
    principal = await get_current_principal()
    if not (principal.is_admin or principal.is_auditor):
        raise ForbiddenError("需要管理员或审计员权限")
    return principal


async def require_non_auditor() -> Principal:
    principal = await get_current_principal()
    if principal.is_auditor:
        raise ForbiddenError("审计员为只读角色，无法执行此操作")
    return principal


async def enforce_auditor_read_only(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    """Default-deny all authenticated mutations for the read-only auditor role."""
    if principal.is_auditor and request.method.upper() not in {
        "GET",
        "HEAD",
        "OPTIONS",
    }:
        raise ForbiddenError("审计员为只读角色，无法执行此操作")
    return principal


async def get_workspace_context(
        x_workspace_id: str | None = Header(default=None, alias="X-Workspace-Id"),
) -> WorkspaceContext:
    principal = await get_current_principal()
    workspace_id = (x_workspace_id or "").strip()
    if not workspace_id:
        context = WorkspaceContext(
            principal=principal,
            scope=OwnerScope.personal(principal.user_id),
        )
        set_authorization_context(
            AuthorizationContext.for_principal(
                principal,
                scope=context.scope,
                request_id=get_request_id() or "",
            )
        )
        return context
    if workspace_id not in principal.team_roles:
        raise ForbiddenError("无权访问该工作区", error_key="errors.workspaceAccessDenied")
    context = WorkspaceContext(
        principal=principal,
        scope=OwnerScope.team(principal.user_id, workspace_id),
    )
    set_authorization_context(
        AuthorizationContext.for_principal(
            principal,
            scope=context.scope,
            request_id=get_request_id() or "",
        )
    )
    return context


async def verify_csrf(request: Request) -> None:
    CsrfService().verify_request(request)


async def require_service_api_key(
        x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
) -> Principal:
    if not x_api_key:
        raise UnauthorizedError("缺少服务 API Key")
    key_hash = ServiceApiKeyHasher().hash(x_api_key)
    async with get_uow() as uow:
        key = await uow.service_api_key.get_by_hash(key_hash)
        if not key:
            raise UnauthorizedError("服务 API Key 无效")
        user = await uow.user.get_by_id(key.owner_user_id)
        if not user or not user.is_active:
            raise UnauthorizedError("服务 API Key 所属账号不可用")
        principal = Principal(
            user_id=user.id,
            global_role=user.global_role,
            token_version=user.token_version,
            team_roles={},
        )
        if principal.is_auditor:
            raise ForbiddenError(
                "审计员为只读角色，无法使用服务 API Key 执行操作"
            )
        set_principal(principal)
        set_authorization_context(
            AuthorizationContext.for_principal(
                principal,
                request_id=get_request_id() or "",
            )
        )
        return principal
