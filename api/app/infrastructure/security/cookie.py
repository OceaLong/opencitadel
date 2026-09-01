import secrets

from starlette.responses import Response

from app.application.ports.crypto import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    host_cookie_name,
)


class AuthCookieManager:
    def __init__(
        self,
        *,
        domain: str | None = None,
        secure: bool = True,
        same_site: str = "lax",
        access_max_age: int = 900,
        refresh_max_age: int = 60 * 60 * 24 * 30,
    ) -> None:
        self.domain = domain or None
        self.secure = secure
        self.same_site = same_site
        self.access_max_age = access_max_age
        self.refresh_max_age = refresh_max_age
        # When the `__Host-` prefix is in effect the cookie MUST be Secure, Path=/,
        # and carry no Domain attribute. Resolve that once so the emitted name and
        # its attributes can never drift apart.
        self._host_prefixed = bool(secure) and not self.domain
        self._secure = True if self._host_prefixed else self.secure
        self._domain = None if self._host_prefixed else self.domain

    def _cookie_name(self, base_name: str) -> str:
        return host_cookie_name(base_name, cookie_domain=self.domain, cookie_secure=self.secure)

    def set_auth_cookies(self, response: Response, *, access_token: str, refresh_token: str) -> str:
        csrf_token = secrets.token_urlsafe(32)
        response.set_cookie(
            self._cookie_name(ACCESS_COOKIE),
            access_token,
            max_age=self.access_max_age,
            httponly=True,
            secure=self._secure,
            samesite=self.same_site,
            domain=self._domain,
            path="/",
        )
        response.set_cookie(
            self._cookie_name(REFRESH_COOKIE),
            refresh_token,
            max_age=self.refresh_max_age,
            httponly=True,
            secure=self._secure,
            samesite=self.same_site,
            domain=self._domain,
            path="/",
        )
        response.set_cookie(
            self._cookie_name(CSRF_COOKIE),
            csrf_token,
            max_age=self.refresh_max_age,
            httponly=False,
            secure=self._secure,
            samesite=self.same_site,
            domain=self._domain,
            path="/",
        )
        return csrf_token

    def clear_auth_cookies(self, response: Response) -> None:
        for base_name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
            response.delete_cookie(self._cookie_name(base_name), domain=self._domain, path="/")
