"""Security-event counters are registered and increment monotonically.

REGISTRY sample-value diffs keep the assertions robust against test-order /
parallel pollution across the process.
"""

from prometheus_client import REGISTRY

from app.interfaces.observability.security_metrics import (
    AUTH_LOGIN_FAILURES,
    AUTH_TOKEN_REJECTED,
    CSRF_FAILURES,
    RATE_LIMIT_REJECTED,
    SERVICE_KEY_AUTH_FAILURES,
    record_csrf_failure,
    record_login_failure,
    record_rate_limit_rejected,
    record_service_key_auth_failure,
    record_token_rejected,
)


def _value(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_security_counters_are_registered():
    assert AUTH_LOGIN_FAILURES is not None
    assert AUTH_TOKEN_REJECTED is not None
    assert CSRF_FAILURES is not None
    assert RATE_LIMIT_REJECTED is not None
    assert SERVICE_KEY_AUTH_FAILURES is not None


def test_record_login_failure_increments_by_reason():
    before = _value("auth_login_failures_total", {"reason": "invalid_credentials"})
    record_login_failure("invalid_credentials")
    after = _value("auth_login_failures_total", {"reason": "invalid_credentials"})
    assert after - before == 1.0


def test_record_token_rejected_increments_by_reason():
    before = _value("auth_token_rejected_total", {"reason": "decode_error"})
    record_token_rejected("decode_error")
    after = _value("auth_token_rejected_total", {"reason": "decode_error"})
    assert after - before == 1.0


def test_record_csrf_failure_increments():
    before = _value("csrf_failures_total", {})
    record_csrf_failure()
    after = _value("csrf_failures_total", {})
    assert after - before == 1.0


def test_record_rate_limit_rejected_increments_by_bucket():
    before = _value("rate_limit_rejected_total", {"bucket": "auth"})
    record_rate_limit_rejected("auth")
    after = _value("rate_limit_rejected_total", {"bucket": "auth"})
    assert after - before == 1.0


def test_record_service_key_auth_failure_increments():
    before = _value("service_key_auth_failures_total", {})
    record_service_key_auth_failure()
    after = _value("service_key_auth_failures_total", {})
    assert after - before == 1.0
