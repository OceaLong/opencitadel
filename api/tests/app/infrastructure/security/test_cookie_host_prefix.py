"""`__Host-` cookie prefix (F16b) — write/read consistency and login/CSRF smoke.

The prefix defeats a sibling-subdomain overwrite of the CSRF double-submit cookie,
but only works when the browser can enforce Secure + Path=/ + no Domain. These
tests pin both halves: the write side emits the right name and attributes, and the
central read helper recovers the value the write side emitted for every config.
"""

from http.cookies import SimpleCookie

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse, Response

from app.application.ports.crypto import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    CSRF_HEADER,
    REFRESH_COOKIE,
    host_cookie_name,
    read_host_cookie,
)
from app.domain.errors import ForbiddenError
from app.infrastructure.security.cookie import AuthCookieManager
from app.infrastructure.security.csrf import CsrfService


def _set_cookies(manager: AuthCookieManager) -> SimpleCookie:
    response = Response()
    manager.set_auth_cookies(response, access_token="a-tok", refresh_token="r-tok")
    jar: SimpleCookie = SimpleCookie()
    for header in response.raw_headers:
        if header[0].lower() == b"set-cookie":
            jar.load(header[1].decode("latin-1"))
    return jar


# --- write side ----------------------------------------------------------------


def test_secure_without_domain_uses_host_prefix_and_locks_attributes() -> None:
    jar = _set_cookies(AuthCookieManager(domain=None, secure=True))

    for base in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        name = f"__Host-{base}"
        assert name in jar, f"expected {name} to be written"
        morsel = jar[name]
        assert morsel["secure"], "__Host- cookies must be Secure"
        assert morsel["path"] == "/", "__Host- cookies must have Path=/"
        assert morsel["domain"] == "", "__Host- cookies must carry no Domain"

    # The bare base names must NOT be present when the prefix is in effect.
    for base in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        assert base not in jar


def test_configured_domain_uses_base_names_and_sets_domain() -> None:
    jar = _set_cookies(AuthCookieManager(domain="example.com", secure=True))

    for base in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        assert base in jar, f"expected bare {base} when a Domain is configured"
        assert jar[base]["domain"] == "example.com"
        assert f"__Host-{base}" not in jar


def test_insecure_dev_uses_base_names() -> None:
    # cookie_secure defaults to False in dev/http; the prefix requires Secure, so
    # the bare names are used and nothing forces Secure (which would break http).
    jar = _set_cookies(AuthCookieManager(domain=None, secure=False))

    for base in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        assert base in jar
        assert not jar[base]["secure"]
        assert f"__Host-{base}" not in jar


# --- read/write consistency ----------------------------------------------------


def test_read_helper_recovers_written_name_across_configs() -> None:
    for domain, secure in ((None, True), ("example.com", True), (None, False)):
        jar = _set_cookies(AuthCookieManager(domain=domain, secure=secure))
        cookies = {morsel.key: morsel.value for morsel in jar.values()}
        # host_cookie_name (write) and read_host_cookie (read) agree.
        assert read_host_cookie(cookies, ACCESS_COOKIE) == "a-tok"
        assert read_host_cookie(cookies, REFRESH_COOKIE) == "r-tok"
        expected = host_cookie_name(ACCESS_COOKIE, cookie_domain=domain, cookie_secure=secure)
        assert expected in cookies


def test_read_helper_prefers_host_prefixed_over_shadow_bare_cookie() -> None:
    # A sibling subdomain could plant a bare `csrf_token` on a shared parent
    # domain; the host-locked `__Host-` value must win the read.
    cookies = {"__Host-csrf_token": "legit", "csrf_token": "attacker"}
    assert read_host_cookie(cookies, CSRF_COOKIE) == "legit"


# --- login + CSRF smoke (real round-trip over https) ---------------------------


def _smoke_app() -> FastAPI:
    manager = AuthCookieManager(domain=None, secure=True)
    csrf = CsrfService()
    app = FastAPI()

    @app.post("/login")
    def login() -> Response:
        response = JSONResponse({})
        token = manager.set_auth_cookies(response, access_token="access-jwt", refresh_token="r")
        response.body = response.render({"csrf": token})
        response.headers["content-length"] = str(len(response.body))
        return response

    @app.get("/whoami")
    def whoami(request: Request) -> Response:
        # Read side recovers the access cookie the login write emitted.
        return JSONResponse({"token": read_host_cookie(request.cookies, ACCESS_COOKIE)})

    @app.post("/mutate")
    def mutate(request: Request) -> Response:
        try:
            csrf.verify_request(request)
        except ForbiddenError:
            return JSONResponse({"ok": False}, status_code=403)
        return JSONResponse({"ok": True})

    return app


def test_login_and_csrf_round_trip_with_host_prefix() -> None:
    # https base_url so the TestClient's cookie jar sends the Secure __Host- cookies.
    with TestClient(_smoke_app(), base_url="https://testserver") as client:
        login = client.post("/login")
        assert login.status_code == 200
        csrf_token = login.json()["csrf"]
        # The browser-visible cookie is the __Host- prefixed name.
        assert "__Host-csrf_token" in login.cookies
        assert "csrf_token" not in {k for k in login.cookies if not k.startswith("__Host-")}

        # Read side resolves the access cookie written under the __Host- name.
        who = client.get("/whoami")
        assert who.json()["token"] == "access-jwt"

        # Double-submit succeeds when the header echoes the __Host- csrf cookie.
        ok = client.post("/mutate", headers={CSRF_HEADER: csrf_token})
        assert ok.status_code == 200

        # And fails on mismatch, proving the compare is real.
        bad = client.post("/mutate", headers={CSRF_HEADER: "wrong"})
        assert bad.status_code == 403
