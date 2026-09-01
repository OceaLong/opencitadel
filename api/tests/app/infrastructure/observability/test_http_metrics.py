"""HTTP metrics: counters/histograms are registered and increment monotonically.

Uses REGISTRY sample-value diffs (not absolutes) so test order / parallel
pollution across the process never flips these assertions.
"""

from prometheus_client import REGISTRY

from app.interfaces.observability.http_metrics import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    record_http_request,
)


def _counter_value(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _histogram_count(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(f"{name}_count", labels) or 0.0


def test_http_metrics_are_registered():
    assert HTTP_REQUESTS_TOTAL is not None
    assert HTTP_REQUEST_DURATION_SECONDS is not None


def test_record_http_request_increments_counter_and_histogram():
    counter_labels = {"method": "GET", "route": "/sessions/{id}", "status": "200"}
    hist_labels = {"method": "GET", "route": "/sessions/{id}"}
    before_count = _counter_value("http_requests_total", counter_labels)
    before_hist = _histogram_count("http_request_duration_seconds", hist_labels)

    record_http_request(method="GET", route="/sessions/{id}", status=200, duration_seconds=0.01)

    after_count = _counter_value("http_requests_total", counter_labels)
    after_hist = _histogram_count("http_request_duration_seconds", hist_labels)
    assert after_count - before_count == 1.0
    assert after_hist - before_hist == 1.0


def test_record_http_request_with_none_duration_skips_histogram_observe():
    counter_labels = {"method": "POST", "route": "/x", "status": "500"}
    hist_labels = {"method": "POST", "route": "/x"}
    before_count = _counter_value("http_requests_total", counter_labels)
    before_hist = _histogram_count("http_request_duration_seconds", hist_labels)

    record_http_request(method="POST", route="/x", status=500, duration_seconds=None)

    after_count = _counter_value("http_requests_total", counter_labels)
    after_hist = _histogram_count("http_request_duration_seconds", hist_labels)
    assert after_count - before_count == 1.0
    assert after_hist - before_hist == 0.0
