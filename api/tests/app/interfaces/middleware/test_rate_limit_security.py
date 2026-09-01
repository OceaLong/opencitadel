import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

from app.application.ports.coordination import RateLimitDecision, RedisConnectivity
from app.composition.types import ApiRuntime
from app.domain.runtime_policy import OperationsPolicy, TrafficPolicy
from app.interfaces.middleware.rate_limit import (
    RateLimitBackendUnavailable,
    RateLimitMiddleware,
)
from tests.runtime_policy_support import MutablePolicyReader


def _request(
    peer: str,
    forwarded_for: str = "",
    cookie: str = "",
    path: str = "/api/auth/login",
    api_key: str = "",
    runtime: ApiRuntime | None = None,
) -> Request:
    headers = [(b"x-forwarded-for", forwarded_for.encode("ascii"))] if forwarded_for else []
    if cookie:
        headers.append((b"cookie", cookie.encode("ascii")))
    if api_key:
        headers.append((b"x-api-key", api_key.encode("ascii")))
    application = Starlette()
    if runtime is not None:
        application.state.runtime = runtime
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
            "app": application,
        }
    )


def _runtime(*, reader, store) -> ApiRuntime:
    runtime = object.__new__(ApiRuntime)
    object.__setattr__(runtime, "runtime_policy_reader", reader)
    object.__setattr__(runtime, "rate_limit_store", store)
    return runtime


class _UnavailableStore:
    async def check_and_record(self, _key: str, *, limit: int, window_seconds: int):
        assert limit > 0
        assert window_seconds == 60
        return RateLimitDecision(False, RedisConnectivity(False, "redis_unavailable"))


def test_rate_limit_key_ignores_spoofed_header_from_untrusted_peer():
    middleware = RateLimitMiddleware(
        Starlette(),
        trusted_proxy_cidrs=("10.0.0.0/8",),
    )

    assert middleware._client_key(_request("203.0.113.10", "1.2.3.4")) == "203.0.113.10"


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
async def test_production_rate_limit_fails_closed_when_redis_is_unavailable():
    reader = MutablePolicyReader()
    request = _request(
        "203.0.113.10",
        runtime=_runtime(reader=reader, store=_UnavailableStore()),
    )
    middleware = RateLimitMiddleware(
        Starlette(),
        fail_closed=True,
    )

    with pytest.raises(RateLimitBackendUnavailable):
        await middleware._is_rate_limited(request, "203.0.113.10", limit=120)


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

    assert middleware._is_limited_path("/api/health/live") is False
    assert middleware._is_limited_path("/api/health/ready") is False
    assert middleware._is_limited_path("/api/health/private") is True
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
    assert all(
        secret not in "\n".join(keys)
        for secret in (
            "random-access",
            "stable-refresh",
            "stable-service-key",
        )
    )


@pytest.mark.asyncio
async def test_one_request_reads_one_current_traffic_policy() -> None:
    reader = MutablePolicyReader(
        operations=OperationsPolicy(
            traffic=TrafficPolicy(requests_per_minute=1),
        )
    )
    runtime = _runtime(reader=reader, store=_UnavailableStore())
    middleware = RateLimitMiddleware(Starlette())

    first = await middleware._traffic_policy(_request("203.0.113.10", runtime=runtime))
    reader.set_operations(
        OperationsPolicy(
            traffic=TrafficPolicy(requests_per_minute=9),
        )
    )
    second = await middleware._traffic_policy(_request("203.0.113.10", runtime=runtime))

    assert first.requests_per_minute == 1
    assert second.requests_per_minute == 9
    assert [require_fresh for require_fresh, _now in reader.operations_calls] == [True, True]


@pytest.mark.asyncio
async def test_live_limit_tightening_applies_to_the_next_request() -> None:
    reader = MutablePolicyReader(
        operations=OperationsPolicy(
            traffic=TrafficPolicy(requests_per_minute=2),
        )
    )
    runtime = _runtime(reader=reader, store=_UnavailableStore())
    middleware = RateLimitMiddleware(Starlette())

    async def accepted(_request):
        return Response(status_code=204)

    first = await middleware.dispatch(
        _request("203.0.113.10", path="/api/sessions", runtime=runtime),
        accepted,
    )
    reader.set_operations(
        OperationsPolicy(
            traffic=TrafficPolicy(requests_per_minute=1),
        )
    )
    denied = await middleware.dispatch(
        _request("203.0.113.10", path="/api/sessions", runtime=runtime),
        accepted,
    )

    assert first.status_code == 204
    assert denied.status_code == 429
    assert len(reader.operations_calls) == 2


def test_auth_bucket_uses_dedicated_auth_budget_not_the_general_one() -> None:
    """Credential endpoints must draw from the tighter auth budget so a
    generous business `requests_per_minute` cannot open a brute-force window."""
    traffic = TrafficPolicy(requests_per_minute=500, auth_requests_per_minute=7)

    assert RateLimitMiddleware._bucket_limit(traffic, "auth") == 7
    assert RateLimitMiddleware._bucket_limit(traffic, "api") == 500
    assert RateLimitMiddleware._bucket_limit(traffic, "files") == 500


@pytest.mark.asyncio
async def test_auth_endpoint_denied_by_tighter_auth_budget() -> None:
    """/api/auth/* is throttled at auth_requests_per_minute even when the
    general per-minute budget is generous, and the 429 advertises that limit."""
    reader = MutablePolicyReader(
        operations=OperationsPolicy(
            traffic=TrafficPolicy(requests_per_minute=500, auth_requests_per_minute=1),
        )
    )
    runtime = _runtime(reader=reader, store=_UnavailableStore())
    middleware = RateLimitMiddleware(Starlette())

    async def accepted(_request):
        return Response(status_code=204)

    first = await middleware.dispatch(
        _request("203.0.113.10", path="/api/auth/login", runtime=runtime),
        accepted,
    )
    denied = await middleware.dispatch(
        _request("203.0.113.10", path="/api/auth/login", runtime=runtime),
        accepted,
    )

    assert first.status_code == 204
    assert denied.status_code == 429
    assert denied.headers["X-RateLimit-Limit"] == "1"


@pytest.mark.asyncio
async def test_business_endpoint_keeps_general_budget_under_tight_auth_limit() -> None:
    """A tight auth budget must not bleed into business traffic: /api/sessions
    still rides the larger requests_per_minute allowance."""
    reader = MutablePolicyReader(
        operations=OperationsPolicy(
            traffic=TrafficPolicy(requests_per_minute=500, auth_requests_per_minute=1),
        )
    )
    runtime = _runtime(reader=reader, store=_UnavailableStore())
    middleware = RateLimitMiddleware(Starlette())

    async def accepted(_request):
        return Response(status_code=204)

    first = await middleware.dispatch(
        _request("203.0.113.10", path="/api/sessions", runtime=runtime),
        accepted,
    )
    second = await middleware.dispatch(
        _request("203.0.113.10", path="/api/sessions", runtime=runtime),
        accepted,
    )

    assert first.status_code == 204
    assert second.status_code == 204
