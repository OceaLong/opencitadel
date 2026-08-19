#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from app.interfaces.middleware.rate_limit import (
    RateLimitBackendUnavailable,
    RateLimitMiddleware,
)


def _request(
    peer: str,
    forwarded_for: str = "",
    cookie: str = "",
    path: str = "/api/auth/login",
    api_key: str = "",
) -> Request:
    headers = (
        [(b"x-forwarded-for", forwarded_for.encode("ascii"))]
        if forwarded_for
        else []
    )
    if cookie:
        headers.append((b"cookie", cookie.encode("ascii")))
    if api_key:
        headers.append((b"x-api-key", api_key.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": headers,
            "client": (peer, 12345),
            "server": ("api", 8000),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_rate_limit_key_ignores_spoofed_header_from_untrusted_peer():
    middleware = RateLimitMiddleware(
        Starlette(),
        trusted_proxy_cidrs=("10.0.0.0/8",),
    )

    assert (
        middleware._client_key(_request("203.0.113.10", "1.2.3.4"))
        == "203.0.113.10"
    )


def test_authenticated_requests_are_limited_by_ip_and_opaque_credential():
    middleware = RateLimitMiddleware(Starlette())
    request = _request(
        "203.0.113.10",
        cookie="access_token=highly-sensitive-jwt",
    )

    keys = middleware._request_keys(request)

    assert len(keys) == 2
    assert keys[0] == "ip:203.0.113.10:auth"
    assert keys[1].startswith("credential:")
    assert "highly-sensitive-jwt" not in keys[1]

@pytest.mark.asyncio
async def test_production_rate_limit_fails_closed_when_redis_is_unavailable(
    monkeypatch,
):
    def unavailable():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(
        "app.infrastructure.storage.redis.get_redis",
        unavailable,
    )
    middleware = RateLimitMiddleware(
        Starlette(),
        fail_closed=True,
    )

    with pytest.raises(RateLimitBackendUnavailable):
        await middleware._is_rate_limited_redis("203.0.113.10")


def test_all_business_api_paths_and_service_credentials_are_limited():
    middleware = RateLimitMiddleware(Starlette())
    request = _request(
        "203.0.113.10",
        path="/api/sessions",
        api_key="service-secret-value",
    )

    assert middleware._is_limited_path(request.url.path) is True
    keys = middleware._request_keys(request)
    assert len(keys) == 2
    assert keys[0] == "ip:203.0.113.10:api"
    assert keys[1].startswith("credential:")
    assert "service-secret-value" not in keys[1]


def test_health_path_does_not_consume_business_rate_limit():
    middleware = RateLimitMiddleware(Starlette())

    assert middleware._is_limited_path("/api/status") is False
    assert middleware._is_limited_path("/api/status/private") is True


def test_metrics_path_is_no_longer_rate_limit_exempt():
    """/api/metrics now requires a bearer token (see metrics_routes.py), so it
    must not skip the rate limiter like the truly public /api/status probe."""
    middleware = RateLimitMiddleware(Starlette())

    assert middleware._is_limited_path("/api/metrics") is True
    assert middleware._is_limited_path("/api/metrics/export") is True


def test_each_present_credential_gets_its_own_rate_limit_bucket():
    middleware = RateLimitMiddleware(Starlette())
    request = _request(
        "203.0.113.10",
        cookie="access_token=random-access; refresh_token=stable-refresh",
        api_key="stable-service-key",
    )

    keys = middleware._request_keys(request)

    assert len(keys) == 4
    assert len(set(keys)) == 4
    assert all(secret not in "\n".join(keys) for secret in (
        "random-access",
        "stable-refresh",
        "stable-service-key",
    ))


class _FakeServerCfg:
    def __init__(self, per_minute: int, enabled: bool = True):
        self.rate_limit_per_minute = per_minute
        self.rate_limit_enabled = enabled


class _FakeRuntimeCfg:
    def __init__(self, per_minute: int, enabled: bool = True):
        self.server = _FakeServerCfg(per_minute, enabled)


def test_effective_limit_follows_runtime_config(monkeypatch):
    """限流值必须每请求动态读运行时配置，而非安装时冻结（冷缓存 bug 回归测试）。"""
    middleware = RateLimitMiddleware(Starlette(), requests_per_minute=120)
    monkeypatch.setattr(
        "app.interfaces.middleware.rate_limit.get_runtime_config",
        lambda: _FakeRuntimeCfg(per_minute=999),
    )
    assert middleware._effective_limit() == 999


def test_effective_limit_falls_back_to_install_value_on_error(monkeypatch):
    middleware = RateLimitMiddleware(Starlette(), requests_per_minute=77)

    def _boom():
        raise RuntimeError("cold")

    monkeypatch.setattr(
        "app.interfaces.middleware.rate_limit.get_runtime_config", _boom
    )
    assert middleware._effective_limit() == 77


def test_rate_limit_disabled_at_runtime_skips_limiting(monkeypatch):
    middleware = RateLimitMiddleware(Starlette(), requests_per_minute=120)
    monkeypatch.setattr(
        "app.interfaces.middleware.rate_limit.get_runtime_config",
        lambda: _FakeRuntimeCfg(per_minute=120, enabled=False),
    )
    assert middleware._runtime_enabled() is False
