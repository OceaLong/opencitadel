#!/usr/bin/env python
# -*- coding: utf-8 -*-
import time
from collections import defaultdict, deque
from typing import Deque, DefaultDict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from core.config import get_settings

_WINDOW_SECONDS = 60


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory per-IP rate limiter for sensitive public endpoints."""

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
            "/api/marketplace/",
            "/api/rooms/",
            "/api/files",
        )
        return any(path.startswith(prefix) for prefix in prefixes)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "OPTIONS" or not self._is_limited_path(request.url.path):
            return await call_next(request)

        now = time.monotonic()
        key = self._client_key(request)
        bucket = self._hits[key]
        while bucket and now - bucket[0] > _WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= self._limit:
            return JSONResponse(
                status_code=429,
                content={"code": 429, "msg": "请求过于频繁，请稍后再试", "data": None},
            )
        bucket.append(now)
        return await call_next(request)


def maybe_install_rate_limit(app) -> None:
    settings = get_settings()
    if settings.env == "test":
        return
    if settings.rate_limit_enabled:
        app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_per_minute)
