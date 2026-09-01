"""Prometheus metrics for the HTTP interface layer.

Defensive try-import style mirroring ``admission_metrics.py`` / ``governance_metrics.py``:
when ``prometheus_client`` is unavailable every metric object is ``None`` and the
``record_*`` helpers become silent no-ops so callers never need to guard on
availability.

Route labels use the *templated* path (``request.scope["route"].path`` such as
``/sessions/{id}``) rather than the raw request path. Raw paths carry high
cardinality and can leak share tokens / webhook secrets into the metric label
set, so they are never used.
"""

from __future__ import annotations

from starlette.requests import Request

try:
    from prometheus_client import Counter, Histogram
except ImportError:
    Counter = None  # type: ignore
    Histogram = None  # type: ignore

_UNMATCHED_ROUTE = "<unmatched>"

if Counter is not None:
    HTTP_REQUESTS_TOTAL = Counter(
        "http_requests_total",
        "Total HTTP requests handled, by method, templated route, and status",
        ["method", "route", "status"],
    )
    HTTP_REQUEST_DURATION_SECONDS = Histogram(
        "http_request_duration_seconds",
        "HTTP request handling latency in seconds, by method and templated route",
        ["method", "route"],
        buckets=(
            0.005,
            0.01,
            0.025,
            0.05,
            0.1,
            0.25,
            0.5,
            1.0,
            2.5,
            5.0,
            10.0,
            30.0,
        ),
    )
else:
    HTTP_REQUESTS_TOTAL = None
    HTTP_REQUEST_DURATION_SECONDS = None


def route_template(request: Request) -> str:
    """Return the matched route template, or a placeholder for unmatched requests.

    ``request.scope["route"]`` is only populated once routing has run. For
    requests short-circuited by an outer middleware (rate-limit 429, CSRF 403)
    or that never match a route (404) the route is absent, so a stable
    low-cardinality placeholder is used instead of the raw path.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path or _UNMATCHED_ROUTE


def record_http_request(
    method: str,
    route: str,
    status: int,
    duration_seconds: float | None,
) -> None:
    if HTTP_REQUESTS_TOTAL is not None:
        HTTP_REQUESTS_TOTAL.labels(method=method, route=route, status=str(status)).inc()
    if (
        HTTP_REQUEST_DURATION_SECONDS is not None
        and duration_seconds is not None
        and duration_seconds >= 0
    ):
        HTTP_REQUEST_DURATION_SECONDS.labels(method=method, route=route).observe(duration_seconds)
