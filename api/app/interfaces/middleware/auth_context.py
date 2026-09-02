import contextlib
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.application.ports.crypto import ACCESS_COOKIE, read_host_cookie
from app.application.request_context import get_request_id
from app.application.security.authorization_context import (
    reset_authorization_context,
    set_authorization_context,
)
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import Principal
from app.interfaces.auth_context import set_principal
from app.interfaces.service_dependencies import require_api_runtime


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
        return await runtime.identity.auth.principal_from_access(token)
