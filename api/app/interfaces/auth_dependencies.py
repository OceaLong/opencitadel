#!/usr/bin/env python
# -*- coding: utf-8 -*-
from fastapi import Header, Request

from app.application.errors.exceptions import ForbiddenError, UnauthorizedError
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.infrastructure.security.csrf import CsrfService
from app.infrastructure.security.service_api_key import ServiceApiKeyHasher
from app.infrastructure.storage.postgres import get_uow
from app.interfaces.auth_context import get_principal


async def get_current_principal() -> Principal:
    principal = get_principal()
    if principal is None:
        raise UnauthorizedError()
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


async def get_workspace_context(
        x_workspace_id: str | None = Header(default=None, alias="X-Workspace-Id"),
) -> WorkspaceContext:
    principal = await get_current_principal()
    workspace_id = (x_workspace_id or "").strip()
    if not workspace_id:
        return WorkspaceContext(principal=principal, scope=OwnerScope.personal(principal.user_id))
    if workspace_id not in principal.team_roles:
        raise ForbiddenError("无权访问该工作区", error_key="errors.workspaceAccessDenied")
    return WorkspaceContext(principal=principal, scope=OwnerScope.team(principal.user_id, workspace_id))


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
        return Principal(
            user_id=user.id,
            global_role=user.global_role,
            token_version=user.token_version,
            team_roles={},
        )
