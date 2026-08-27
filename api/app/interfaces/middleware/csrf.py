"""Runtime-backed double-submit CSRF middleware."""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.application.ports.crypto import ACCESS_COOKIE, REFRESH_COOKIE
from app.domain.errors import ForbiddenError
from app.interfaces.service_dependencies import require_api_runtime

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class CsrfMiddleware(BaseHTTPMiddleware):
    """Resolve CSRF verification from the lifespan-owned runtime."""

    _EXEMPT_PATHS = frozenset(
        {
            "/api/auth/login",
            "/api/auth/register",
            "/api/auth/refresh",
        }
    )
    _EXEMPT_PREFIXES = ("/api/a2a",)

    def _is_exempt(self, path: str) -> bool:
        return path in self._EXEMPT_PATHS or any(
            path.startswith(prefix) for prefix in self._EXEMPT_PREFIXES
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method.upper() not in SAFE_METHODS and not self._is_exempt(request.url.path):
            has_auth_cookie = bool(
                request.cookies.get(ACCESS_COOKIE) or request.cookies.get(REFRESH_COOKIE)
            )
            if has_auth_cookie:
                try:
                    require_api_runtime(request).csrf_service.verify_request(request)
                except ForbiddenError as exc:
                    return JSONResponse(
                        status_code=403,
                        content={"code": 403, "msg": str(exc), "data": None},
                    )
        return await call_next(request)
