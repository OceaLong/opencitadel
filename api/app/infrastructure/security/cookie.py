#!/usr/bin/env python
# -*- coding: utf-8 -*-
import secrets

from starlette.responses import Response


ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"


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

    def set_auth_cookies(self, response: Response, *, access_token: str, refresh_token: str) -> str:
        csrf_token = secrets.token_urlsafe(32)
        response.set_cookie(
            ACCESS_COOKIE,
            access_token,
            max_age=self.access_max_age,
            httponly=True,
            secure=self.secure,
            samesite=self.same_site,
            domain=self.domain,
            path="/",
        )
        response.set_cookie(
            REFRESH_COOKIE,
            refresh_token,
            max_age=self.refresh_max_age,
            httponly=True,
            secure=self.secure,
            samesite=self.same_site,
            domain=self.domain,
            path="/",
        )
        response.set_cookie(
            CSRF_COOKIE,
            csrf_token,
            max_age=self.refresh_max_age,
            httponly=False,
            secure=self.secure,
            samesite=self.same_site,
            domain=self.domain,
            path="/",
        )
        return csrf_token

    def clear_auth_cookies(self, response: Response) -> None:
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
            response.delete_cookie(name, domain=self.domain, path="/")
