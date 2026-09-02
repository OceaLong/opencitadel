"""Signed database authorization binding for greenfield kernel transactions."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.security.db_authorization import configure_session_authorization
from app.kernel.application.ports import KernelAuthorization
from app.kernel.domain.types import OwnerScopeRef


async def bind_context(
    session: AsyncSession,
    context: AuthorizationContext | None = None,
) -> None:
    """Bind RLS claims when the session factory carries its signing secret."""
    secret = str(session.info.get("database_authorization_signing_secret") or "")
    if secret:
        await configure_session_authorization(session, context, signing_secret=secret)


def command_context(
    authorization: KernelAuthorization,
    scope: OwnerScopeRef,
    *,
    request_id: str,
) -> AuthorizationContext:
    if authorization.is_system:
        return AuthorizationContext.system(authorization.actor_user_id)

    from app.domain.models.scope import OwnerScope, Principal
    from app.domain.models.team import TeamRole
    from app.domain.models.user import GlobalRole

    team_roles = {scope.team_id: TeamRole.MEMBER} if scope.team_id else {}
    principal = Principal(
        user_id=authorization.actor_user_id,
        global_role=GlobalRole.ADMIN if authorization.is_admin else GlobalRole.USER,
        team_roles=team_roles,
    )
    owner_scope = (
        OwnerScope.team(principal.user_id, scope.team_id)
        if scope.team_id
        else OwnerScope.personal(principal.user_id)
    )
    return AuthorizationContext.for_principal(
        principal,
        scope=owner_scope,
        request_id=request_id,
    )


__all__ = ["bind_context", "command_context"]
