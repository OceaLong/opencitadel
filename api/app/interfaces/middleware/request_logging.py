"""HTTP request logging + metrics middleware with request_id propagation.

Installed as the outermost user middleware so that requests short-circuited by
inner middleware (rate-limit 429, CSRF 403) still produce an access log line and
still feed the HTTP metrics.
"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.application.request_context import bind_context
from app.interfaces.client_ip import get_client_ip
from app.interfaces.observability.http_metrics import record_http_request, route_template

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        with bind_context(request_id=request_id):
            response = await call_next(request)
            duration_seconds = time.perf_counter() - start
            # Use the templated route (e.g. /sessions/{id}) rather than the raw
            # path: raw paths are high-cardinality and can carry share tokens /
            # webhook secrets, which must never reach metric labels or logs.
            route = route_template(request)
            record_http_request(
                method=request.method,
                route=route,
                status=response.status_code,
                duration_seconds=duration_seconds,
            )
            logger.info(
                "HTTP %s %s -> %s duration_ms=%.1f user_id=%s workspace_id=%s client_ip=%s",
                request.method,
                route,
                response.status_code,
                duration_seconds * 1000,
                getattr(request.state, "user_id", None) or "-",
                getattr(request.state, "workspace_id", None) or "-",
                self._client_ip(request),
            )
        response.headers["x-request-id"] = request_id
        return response

    @staticmethod
    def _client_ip(request: Request) -> str:
        try:
            return get_client_ip(request) or "-"
        except (OSError, RuntimeError, ValueError):
            return "-"


def install_request_logging(app) -> None:
    app.add_middleware(RequestLoggingMiddleware)
