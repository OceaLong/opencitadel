#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Route-level auth coverage for the Prometheus /api/metrics endpoint.

App-building pattern mirrors
tests/app/interfaces/endpoints/test_compliance_routes.py: a bare FastAPI app
with the real router mounted and only the settings dependency overridden,
exercised through TestClient. fail-closed semantics (spec §A4):
- metrics_token unset -> 404 (feature disabled, endpoint hidden)
- metrics_token set, missing/wrong Authorization -> 401
- metrics_token set, correct `Bearer <token>` -> 200
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import Settings, get_settings
from app.interfaces.endpoints import metrics_routes
from app.interfaces.errors.exception_handlers import register_exception_handlers


def _app(settings: Settings) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(metrics_routes.router)
    app.dependency_overrides[get_settings] = lambda: settings
    return app


def test_metrics_returns_404_when_token_not_configured():
    app = _app(Settings(metrics_token=""))

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 404


def test_metrics_returns_401_when_authorization_header_missing():
    app = _app(Settings(metrics_token="s3cret-token"))

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 401


def test_metrics_returns_401_when_token_is_wrong():
    app = _app(Settings(metrics_token="s3cret-token"))

    with TestClient(app) as client:
        response = client.get(
            "/metrics", headers={"Authorization": "Bearer wrong-token"}
        )

    assert response.status_code == 401


def test_metrics_returns_200_when_token_is_correct():
    # Import to ensure governance_metrics.py's module-level Counter/Histogram
    # definitions (including governance_policy_denials_total) have run and
    # registered themselves against the default Prometheus registry before
    # the endpoint renders it -- prometheus_client still emits `# HELP` /
    # `# TYPE` lines for a registered family with zero samples, so this is a
    # real assertion that the governance_* metric family (M6) is wired into
    # /api/metrics, not just an import-order accident.
    from app.infrastructure.observability import governance_metrics  # noqa: F401

    app = _app(Settings(metrics_token="s3cret-token"))

    with TestClient(app) as client:
        response = client.get(
            "/metrics", headers={"Authorization": "Bearer s3cret-token"}
        )

    assert response.status_code == 200
    assert "governance_policy_denials_total" in response.text
