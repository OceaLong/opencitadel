import contextlib
import logging
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.application.ports.crypto import ACCESS_COOKIE, TokenCodecError, read_host_cookie
from app.application.request_context import get_request_id
from app.application.security.authorization_context import (
    authorization_scope,
    reset_authorization_context,
    set_authorization_context,
)
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import Principal
from app.domain.models.user import UserStatus
from app.interfaces.auth_context import set_principal
from app.interfaces.observability.security_metrics import record_token_rejected
from app.interfaces.service_dependencies import require_api_runtime

logger = logging.getLogger(__name__)


class AuthContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        token = read_host_cookie(request.cookies, ACCESS_COOKIE)
        principal_token = set_principal(None)
        authorization_token = set_authorization_context(AuthorizationContext.anonymous())
        try:
            if token:
                principal = await self._principal_from_token(
                    token,
                    runtime=require_api_runtime(request),
                )
                set_principal(principal)
                if principal is not None:
                    # Expose the resolved subject to the outermost request-logging
                    # middleware, which runs after this middleware has already
                    # reset its context vars.
                    request.state.user_id = principal.user_id
                    set_authorization_context(
                        AuthorizationContext.for_principal(
                            principal,
                            request_id=get_request_id() or "",
                        )
                    )
            return await call_next(request)
        finally:
            with contextlib.suppress(Exception):
                reset_authorization_context(authorization_token)
            with contextlib.suppress(Exception):
                principal_token.var.reset(principal_token)

    async def _principal_from_token(self, token: str, *, runtime) -> Principal | None:
        try:
            claims = runtime.token_codec.decode(token, expected_type="access")
        except TokenCodecError:
            record_token_rejected("decode_error")
            return None
        user_id = str(claims.get("sub") or "")
        if not user_id:
            record_token_rejected("decode_error")
            return None
        try:
            # Resolving a principal must read users/teams/team_members before the
            # request has any user identity, so it runs under a trusted system
            # scope; RLS on those tables would otherwise deny the anonymous read
            # and lock every authenticated request out.
            with authorization_scope(AuthorizationContext.system("auth-context")):
                async with runtime.uow_factory() as uow:
                    user = await uow.user.get_by_id(user_id)
                    if not user or user.status != UserStatus.ACTIVE:
                        record_token_rejected("user_inactive")
                        return None
                    if int(claims.get("ver", -1)) != user.token_version:
                        record_token_rejected("token_version_mismatch")
                        return None
                    teams = await uow.team.list_for_user(user_id)
                    team_roles = {}
                    for team in teams:
                        member = await uow.team.get_member(team.id, user_id)
                        if member:
                            team_roles[team.id] = member.role
                    return Principal(
                        user_id=user.id,
                        global_role=user.global_role,
                        token_version=user.token_version,
                        team_roles=team_roles,
                    )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("auth context lookup failed: %s", exc)
            record_token_rejected("lookup_error")
            return None
