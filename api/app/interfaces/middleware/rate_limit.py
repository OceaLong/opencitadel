import hashlib
import logging
import time
from collections import defaultdict, deque

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.application.ports.crypto import ACCESS_COOKIE, REFRESH_COOKIE, read_host_cookie
from app.domain.runtime_policy import (
    RuntimePolicyIntegrityError,
    RuntimePolicyStaleError,
    RuntimePolicyUnavailableError,
    TrafficPolicy,
)
from app.domain.utils.time_utils import utc_now
from app.interfaces.client_ip import get_client_ip
from app.interfaces.observability.security_metrics import record_rate_limit_rejected
from app.interfaces.schemas import Response as ApiResponse
from app.interfaces.service_dependencies import require_api_runtime

logger = logging.getLogger(__name__)
_WINDOW_SECONDS = 60


class RateLimitBackendUnavailable(RuntimeError):
    pass


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Distributed per-IP limiter; production fails closed if Redis is down."""

    def __init__(
        self,
        app,
        *,
        fail_closed: bool = False,
        trusted_proxy_cidrs: tuple[str, ...] = (),
    ) -> None:
        super().__init__(app)
        self._fail_closed = fail_closed
        self._trusted_proxy_cidrs = trusted_proxy_cidrs
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

    async def _traffic_policy(self, request: Request) -> TrafficPolicy:
        runtime = require_api_runtime(request)
        active = await runtime.runtime_policy_reader.active_operations(
            require_fresh=True,
            now=utc_now(),
        )
        return active.revision.policy.traffic

    def _client_key(self, request: Request) -> str:
        return (
            get_client_ip(
                request,
                trusted_proxy_cidrs=self._trusted_proxy_cidrs,
            )
            or "unknown"
        )

    def _request_keys(self, request: Request) -> tuple[str, ...]:
        """Apply both network and credential limits without storing raw tokens."""
        bucket = self._path_bucket(request.url.path)
        keys = [f"ip:{self._client_key(request)}:{bucket}"]
        tokens = (
            read_host_cookie(request.cookies, ACCESS_COOKIE),
            read_host_cookie(request.cookies, REFRESH_COOKIE),
            request.headers.get("x-api-key"),
        )
        seen_fingerprints: set[str] = set()
        for token in tokens:
            if not token:
                continue
            fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
            if fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)
            keys.append(f"credential:{fingerprint}:{bucket}")
        return tuple(keys)

    def _is_limited_path(self, path: str) -> bool:
        excluded_paths = {
            "/api/health/live",
            "/api/health/ready",
            "/api/status",
        }
        return path.startswith("/api/") and path not in excluded_paths

    async def _is_rate_limited(self, request: Request, key: str, *, limit: int) -> bool:
        try:
            decision = await require_api_runtime(request).rate_limit_store.check_and_record(
                key,
                limit=limit,
                window_seconds=_WINDOW_SECONDS,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            if self._fail_closed:
                logger.error("Redis rate limit unavailable; rejecting request")
                raise RateLimitBackendUnavailable from exc
            logger.debug("Redis rate limit unavailable, using in-memory fallback: %s", exc)
            return await self._is_rate_limited_memory(key, limit=limit)
        if decision.connectivity.available:
            return decision.limited
        if self._fail_closed:
            logger.error("Redis rate limit unavailable; rejecting request")
            raise RateLimitBackendUnavailable
        return await self._is_rate_limited_memory(key, limit=limit)

    async def _is_rate_limited_memory(self, key: str, *, limit: int) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > _WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= limit:
            return True
        bucket.append(now)
        return False

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "OPTIONS" or not self._is_limited_path(request.url.path):
            return await call_next(request)
        try:
            traffic = await self._traffic_policy(request)
        except (
            RuntimePolicyIntegrityError,
            RuntimePolicyStaleError,
            RuntimePolicyUnavailableError,
        ) as exc:
            return JSONResponse(
                status_code=503,
                content=ApiResponse(
                    code=503,
                    msg="运行策略暂不可用，请稍后重试",
                    data={},
                    error_key=exc.error_key,
                ).model_dump(exclude_none=True),
                headers={"Retry-After": "5", "Cache-Control": "no-store"},
            )
        if not traffic.rate_limit_enabled:
            return await call_next(request)

        bucket = self._path_bucket(request.url.path)
        limit = self._bucket_limit(traffic, bucket)
        try:
            limited = False
            for key in self._request_keys(request):
                if await self._is_rate_limited(
                    request,
                    key,
                    limit=limit,
                ):
                    limited = True
        except RateLimitBackendUnavailable:
            return JSONResponse(
                status_code=503,
                content=ApiResponse(
                    code=503,
                    msg="请求限流服务暂不可用，请稍后重试",
                    data={},
                    error_key="apiErrors.rateLimitBackendUnavailable",
                ).model_dump(exclude_none=True),
                headers={
                    "Retry-After": "5",
                    "Cache-Control": "no-store",
                },
            )
        if limited:
            record_rate_limit_rejected(bucket)
            return JSONResponse(
                status_code=429,
                content=ApiResponse(
                    code=429,
                    msg="请求过于频繁，请稍后再试",
                    data={},
                    error_key="apiErrors.rateLimited",
                ).model_dump(exclude_none=True),
                headers={
                    "Retry-After": str(_WINDOW_SECONDS),
                    "X-RateLimit-Limit": str(limit),
                    "Cache-Control": "no-store",
                },
            )
        return await call_next(request)

    @staticmethod
    def _bucket_limit(traffic: TrafficPolicy, bucket: str) -> int:
        """Credential endpoints get a tighter per-minute budget than business
        traffic so password guessing cannot ride the general API allowance."""
        if bucket == "auth":
            return traffic.auth_requests_per_minute
        return traffic.requests_per_minute

    @staticmethod
    def _path_bucket(path: str) -> str:
        if path.startswith("/api/auth/"):
            return "auth"
        if path.startswith("/api/files"):
            return "files"
        return "api"


def maybe_install_rate_limit(
    app,
    *,
    fail_closed: bool,
    trusted_proxy_cidrs: tuple[str, ...],
) -> None:
    app.add_middleware(
        RateLimitMiddleware,
        fail_closed=fail_closed,
        trusted_proxy_cidrs=trusted_proxy_cidrs,
    )
