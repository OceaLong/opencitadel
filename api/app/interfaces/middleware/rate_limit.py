#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import time
from collections import defaultdict, deque
from typing import Deque, DefaultDict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.application.services.config_provider import get_runtime_config
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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP rate limiter backed by Redis (falls back to in-memory per process)."""

    def __init__(self, app, *, requests_per_minute: int = 120) -> None:
        super().__init__(app)
        self._limit = max(1, requests_per_minute)
        self._hits: DefaultDict[str, Deque[float]] = defaultdict(deque)

    def _client_key(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    def _is_limited_path(self, path: str) -> bool:
        prefixes = (
            "/api/auth/",
            "/api/marketplace/",
            "/api/files",
        )
        return any(path.startswith(prefix) for prefix in prefixes)

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
                str(now),
                _WINDOW_SECONDS + 5,
            )
            return int(result or 0) == 1
        except Exception as exc:
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

        key = self._client_key(request)
        if await self._is_rate_limited_redis(key):
            return JSONResponse(
                status_code=429,
                content={"code": 429, "msg": "请求过于频繁，请稍后再试", "data": None},
            )
        return await call_next(request)


def maybe_install_rate_limit(app) -> None:
    settings = get_settings()
    if settings.env == "test":
        return
    runtime = get_runtime_config()
    if runtime.server.rate_limit_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            requests_per_minute=runtime.server.rate_limit_per_minute,
        )
