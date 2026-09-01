"""Prometheus counters for security-relevant events at the HTTP boundary.

Defensive try-import style mirroring ``admission_metrics.py`` /
``governance_metrics.py``: when ``prometheus_client`` is unavailable every
metric object is ``None`` and the ``record_*`` helpers become silent no-ops.

All instrumentation lives in the ``interfaces``/``middleware`` layer; the
application/domain services stay unaware of the metric surface.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter
except ImportError:
    Counter = None  # type: ignore

if Counter is not None:
    AUTH_LOGIN_FAILURES = Counter(
        "auth_login_failures_total",
        "Rejected credential logins, by reason",
        ["reason"],
    )
    AUTH_TOKEN_REJECTED = Counter(
        "auth_token_rejected_total",
        "Rejected authentication tokens (cookie/session), by reason",
        ["reason"],
    )
    CSRF_FAILURES = Counter(
        "csrf_failures_total",
        "Requests rejected by CSRF verification",
    )
    RATE_LIMIT_REJECTED = Counter(
        "rate_limit_rejected_total",
        "Requests rejected by the rate limiter, by traffic bucket",
        ["bucket"],
    )
    SERVICE_KEY_AUTH_FAILURES = Counter(
        "service_key_auth_failures_total",
        "Rejected service API key authentication attempts",
    )
else:
    AUTH_LOGIN_FAILURES = None
    AUTH_TOKEN_REJECTED = None
    CSRF_FAILURES = None
    RATE_LIMIT_REJECTED = None
    SERVICE_KEY_AUTH_FAILURES = None


def record_login_failure(reason: str) -> None:
    """reason: invalid_credentials|account_locked|... (best-effort classification)"""
    if AUTH_LOGIN_FAILURES is not None:
        AUTH_LOGIN_FAILURES.labels(reason=reason).inc()


def record_token_rejected(reason: str) -> None:
    """reason: decode_error|user_inactive|token_version_mismatch|lookup_error"""
    if AUTH_TOKEN_REJECTED is not None:
        AUTH_TOKEN_REJECTED.labels(reason=reason).inc()


def record_csrf_failure() -> None:
    if CSRF_FAILURES is not None:
        CSRF_FAILURES.inc()


def record_rate_limit_rejected(bucket: str) -> None:
    """bucket: api|auth|files"""
    if RATE_LIMIT_REJECTED is not None:
        RATE_LIMIT_REJECTED.labels(bucket=bucket).inc()


def record_service_key_auth_failure() -> None:
    if SERVICE_KEY_AUTH_FAILURES is not None:
        SERVICE_KEY_AUTH_FAILURES.inc()
