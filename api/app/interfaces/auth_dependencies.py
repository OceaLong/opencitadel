"""Authentication and workspace authorization dependencies."""

from fastapi import Header, Request

from app.application.request_context import get_request_id
from app.application.security.authorization_context import (
    get_authorization_context,
    set_authorization_context,
)
from app.domain.errors import ForbiddenError, UnauthorizedError
from app.domain.models.authorization import AuthorizationContext, AuthorizationMode
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.interfaces.auth_context import get_principal


async def get_current_principal() -> Principal:
    principal = get_principal()
    if principal is None:
        raise UnauthorizedError()
    if get_authorization_context().mode is AuthorizationMode.ANONYMOUS:
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
        raise ForbiddenError("审计员为只读角色")
    return principal


async def enforce_auditor_read_only(
    request: Request,
) -> None:
    principal = await get_current_principal()
    if principal.is_auditor and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        raise ForbiddenError("审计员为只读角色")


async def get_workspace_context(
    request: Request,
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-Id"),
) -> WorkspaceContext:
    principal = await get_current_principal()
    request.state.user_id = principal.user_id
    team_id = (x_workspace_id or "").strip()
    if team_id:
        if team_id not in principal.team_roles:
            raise ForbiddenError("无权访问该工作区", error_key="errors.workspaceAccessDenied")
        scope = OwnerScope.team(principal.user_id, team_id)
        request.state.workspace_id = team_id
    else:
        scope = OwnerScope.personal(principal.user_id)
    context = WorkspaceContext(principal=principal, scope=scope)
    set_authorization_context(
        AuthorizationContext.for_principal(
            principal,
            scope=scope,
            request_id=get_request_id() or "",
        )
    )
    return context


__all__ = [
    "enforce_auditor_read_only",
    "get_current_principal",
    "get_workspace_context",
    "require_admin",
    "require_auditor_or_admin",
    "require_non_auditor",
]
