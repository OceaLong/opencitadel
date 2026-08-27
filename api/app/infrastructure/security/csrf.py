import hmac

from fastapi import Request

from app.application.ports.crypto import CSRF_COOKIE, CSRF_HEADER
from app.domain.errors import ForbiddenError

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class CsrfService:
    def verify_request(self, request: Request) -> None:
        if request.method.upper() in SAFE_METHODS:
            return
        cookie_token = request.cookies.get(CSRF_COOKIE, "")
        header_token = request.headers.get(CSRF_HEADER, "")
        if (
            not cookie_token
            or not header_token
            or not hmac.compare_digest(cookie_token, header_token)
        ):
            raise ForbiddenError("CSRF 校验失败")
