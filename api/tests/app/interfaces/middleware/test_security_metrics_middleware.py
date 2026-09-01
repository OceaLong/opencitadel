"""Security-event metrics are incremented at the middleware boundary."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.application.ports.crypto import TokenCodecError
from app.composition.types import ApiRuntime
from app.domain.errors import ForbiddenError
from app.interfaces.middleware.auth_context import AuthContextMiddleware
from app.interfaces.middleware.csrf import CsrfMiddleware


def _runtime(**values) -> ApiRuntime:
    runtime = object.__new__(ApiRuntime)
    for name, value in values.items():
        object.__setattr__(runtime, name, value)
    return runtime


def _value(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_auth_context_records_token_rejection_on_decode_error():
    class TokenCodec:
        def decode(self, _token: str, expected_type: str):
            assert expected_type == "access"
            raise TokenCodecError("invalid")

    app = FastAPI()
    app.state.runtime = _runtime(token_codec=TokenCodec(), uow_factory=lambda: None)
    app.add_middleware(AuthContextMiddleware)

    @app.get("/")
    async def root():
        return {"ok": True}

    before = _value("auth_token_rejected_total", {"reason": "decode_error"})
    with TestClient(app) as client:
        client.cookies.set("access_token", "invalid-token")
        response = client.get("/")

    assert response.status_code == 200
    after = _value("auth_token_rejected_total", {"reason": "decode_error"})
    assert after - before == 1.0


def test_csrf_middleware_records_failure_on_forbidden():
    class RejectCsrf:
        def verify_request(self, _request) -> None:
            raise ForbiddenError("CSRF validation failed")

    app = FastAPI()
    app.state.runtime = _runtime(csrf_service=RejectCsrf())
    app.add_middleware(CsrfMiddleware)

    @app.post("/api/resource")
    async def mutate():
        return {"ok": True}

    before = _value("csrf_failures_total", {})
    with TestClient(app) as client:
        client.cookies.set("access_token", "authenticated")
        response = client.post("/api/resource")

    assert response.status_code == 403
    after = _value("csrf_failures_total", {})
    assert after - before == 1.0
