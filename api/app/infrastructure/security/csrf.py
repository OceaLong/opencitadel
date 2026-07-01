#!/usr/bin/env python
# -*- coding: utf-8 -*-
import hmac

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.application.errors.exceptions import ForbiddenError
from app.infrastructure.security.cookie import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-csrf-token"


class CsrfService:
    def verify_request(self, request: Request) -> None:
        if request.method.upper() in SAFE_METHODS:
            return
        cookie_token = request.cookies.get(CSRF_COOKIE, "")
        header_token = request.headers.get(CSRF_HEADER, "")
        if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
            raise ForbiddenError("CSRF 校验失败")


class CsrfMiddleware(BaseHTTPMiddleware):
    """Double-submit CSRF protection for cookie-authenticated mutating requests."""

    _EXEMPT_PATHS = {
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/refresh",
    }
    _EXEMPT_PREFIXES = (
        "/api/a2a",
    )

    def __init__(self, app, csrf_service: CsrfService | None = None) -> None:
        super().__init__(app)
        self._csrf_service = csrf_service or CsrfService()

    def _is_exempt(self, path: str) -> bool:
        return path in self._EXEMPT_PATHS or any(path.startswith(prefix) for prefix in self._EXEMPT_PREFIXES)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method.upper() not in SAFE_METHODS and not self._is_exempt(request.url.path):
            has_auth_cookie = bool(request.cookies.get(ACCESS_COOKIE) or request.cookies.get(REFRESH_COOKIE))
            if has_auth_cookie:
                try:
                    self._csrf_service.verify_request(request)
                except ForbiddenError as exc:
                    return JSONResponse(
                        status_code=403,
                        content={"code": 403, "msg": str(exc), "data": None},
                    )
        return await call_next(request)
