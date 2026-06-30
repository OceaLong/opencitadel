#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""HTTP request logging middleware with request_id propagation."""
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.infrastructure.observability.logging_context import bind_context

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        with bind_context(request_id=request_id):
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "HTTP %s %s -> %s duration_ms=%.1f",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
        response.headers["x-request-id"] = request_id
        return response


def install_request_logging(app) -> None:
    app.add_middleware(RequestLoggingMiddleware)
