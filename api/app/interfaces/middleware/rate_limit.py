#!/usr/bin/env python
# -*- coding: utf-8 -*-
import hashlib
import logging
import time
import uuid
from collections import defaultdict, deque
from typing import Deque, DefaultDict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.application.services.config_provider import get_runtime_config
from app.interfaces.client_ip import (
    configured_trusted_proxy_cidrs,
    get_client_ip,
)
from app.infrastructure.security.cookie import ACCESS_COOKIE, REFRESH_COOKIE
from core.config import get_settings

logger = logging.getLogger(__name__)
_WINDOW_SECONDS = 60
_REDIS_KEY_PREFIX = "ratelimit:"

_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_start = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local ttl = tonumber(ARGV[5])
redis.call('zremrangebyscore', key, 0, window_start)
local count = redis.call('zcard', key)
if count >= limit then
  return 1
end
redis.call('zadd', key, now, member)
redis.call('expire', key, ttl)
return 0
"""


class RateLimitBackendUnavailable(RuntimeError):
    pass


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Distributed per-IP limiter; production fails closed if Redis is down."""

    def __init__(
        self,
        app,
        *,
        requests_per_minute: int = 120,
        fail_closed: bool = False,
        trusted_proxy_cidrs: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(app)
        self._limit = max(1, requests_per_minute)
        self._fail_closed = fail_closed
        self._trusted_proxy_cidrs = (
            trusted_proxy_cidrs
            if trusted_proxy_cidrs is not None
            else configured_trusted_proxy_cidrs()
        )
        self._hits: DefaultDict[str, Deque[float]] = defaultdict(deque)

    def _client_key(self, request: Request) -> str:
        return get_client_ip(
            request,
            trusted_proxy_cidrs=self._trusted_proxy_cidrs,
        ) or "unknown"

    def _request_keys(self, request: Request) -> tuple[str, ...]:
        """Apply both network and credential limits without storing raw tokens."""
        bucket = self._path_bucket(request.url.path)
        keys = [f"ip:{self._client_key(request)}:{bucket}"]
        tokens = (
            request.cookies.get(ACCESS_COOKIE),
            request.cookies.get(REFRESH_COOKIE),
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
            "/api/status",
        }
        return path.startswith("/api/") and path not in excluded_paths

    async def _is_rate_limited_redis(self, key: str) -> bool:
        try:
            from app.infrastructure.storage.redis import get_redis

            redis = get_redis().client
            redis_key = f"{_REDIS_KEY_PREFIX}{key}"
            now = time.time()
            window_start = now - _WINDOW_SECONDS
            result = await redis.eval(
                _SLIDING_WINDOW_LUA,
                1,
                redis_key,
                now,
                window_start,
                self._limit,
                f"{now}:{uuid.uuid4().hex}",
                _WINDOW_SECONDS + 5,
            )
            return int(result or 0) == 1
        except Exception as exc:
            if self._fail_closed:
                logger.error("Redis rate limit unavailable; rejecting request")
                raise RateLimitBackendUnavailable from exc
            logger.debug("Redis rate limit unavailable, using in-memory fallback: %s", exc)
            return await self._is_rate_limited_memory(key)

    async def _is_rate_limited_memory(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > _WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= self._limit:
            return True
        bucket.append(now)
        return False

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "OPTIONS" or not self._is_limited_path(request.url.path):
            return await call_next(request)

        try:
            limited = any(
                [
                    await self._is_rate_limited_redis(key)
                    for key in self._request_keys(request)
                ]
            )
        except RateLimitBackendUnavailable:
            return JSONResponse(
                status_code=503,
                content={
                    "code": 503,
                    "msg": "请求限流服务暂不可用，请稍后重试",
                    "data": None,
                },
                headers={
                    "Retry-After": "5",
                    "Cache-Control": "no-store",
                },
            )
        if limited:
            return JSONResponse(
                status_code=429,
                content={"code": 429, "msg": "请求过于频繁，请稍后再试", "data": None},
                headers={
                    "Retry-After": str(_WINDOW_SECONDS),
                    "X-RateLimit-Limit": str(self._limit),
                    "Cache-Control": "no-store",
                },
            )
        return await call_next(request)

    @staticmethod
    def _path_bucket(path: str) -> str:
        if path.startswith("/api/auth/"):
            return "auth"
        if path.startswith("/api/files"):
            return "files"
        return "api"


def maybe_install_rate_limit(app) -> None:
    settings = get_settings()
    if settings.env == "test":
        return
    runtime = get_runtime_config()
    if runtime.server.rate_limit_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            requests_per_minute=runtime.server.rate_limit_per_minute,
            fail_closed=settings.env.lower() == "production",
        )
