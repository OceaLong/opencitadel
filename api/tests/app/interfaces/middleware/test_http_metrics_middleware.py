"""RequestLoggingMiddleware feeds HTTP metrics with a *templated* route label
and still records requests that never match a route (404)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.interfaces.middleware.request_logging import install_request_logging


def _counter_value(labels: dict) -> float:
    return REGISTRY.get_sample_value("http_requests_total", labels) or 0.0


def _histogram_count(labels: dict) -> float:
    return REGISTRY.get_sample_value("http_request_duration_seconds_count", labels) or 0.0


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def get_item(item_id: str):
        return {"item_id": item_id}

    install_request_logging(app)
    return app


def test_request_logging_records_templated_route_not_raw_path():
    app = _app()
    counter_labels = {"method": "GET", "route": "/items/{item_id}", "status": "200"}
    hist_labels = {"method": "GET", "route": "/items/{item_id}"}
    before_count = _counter_value(counter_labels)
    before_hist = _histogram_count(hist_labels)

    with TestClient(app) as client:
        response = client.get("/items/secret-share-token-123")

    assert response.status_code == 200
    # x-request-id is propagated on the response
    assert response.headers.get("x-request-id")
    # The raw path (with the secret token) must NOT appear as a label.
    assert (
        _counter_value({"method": "GET", "route": "/items/secret-share-token-123", "status": "200"})
        == 0.0
    )
    assert _counter_value(counter_labels) - before_count == 1.0
    assert _histogram_count(hist_labels) - before_hist == 1.0


def test_request_logging_records_unmatched_route_as_placeholder():
    app = _app()
    labels = {"method": "GET", "route": "<unmatched>", "status": "404"}
    before = _counter_value(labels)

    with TestClient(app) as client:
        response = client.get("/no-such-route")

    assert response.status_code == 404
    assert _counter_value(labels) - before == 1.0
