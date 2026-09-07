from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.security.authorization_context import authorization_scope
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import Principal
from app.domain.models.user import GlobalRole
from app.interfaces.auth_context import current_principal
from app.interfaces.auth_dependencies import verify_csrf
from app.interfaces.endpoints.admin_routes import router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_audit_service, get_execution_projection_status


def application():
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def authenticate(request, call_next):
        token = current_principal.set(app.state.principal)
        try:
            with authorization_scope(AuthorizationContext.for_principal(app.state.principal)):
                return await call_next(request)
        finally:
            current_principal.reset(token)

    register_exception_handlers(app)
    status = SimpleNamespace(request_recovery=AsyncMock(return_value="request-1"))
    audit = SimpleNamespace(record=AsyncMock())
    app.dependency_overrides[get_execution_projection_status] = lambda: status
    app.dependency_overrides[get_audit_service] = lambda: audit
    app.dependency_overrides[verify_csrf] = lambda: None
    return app, status, audit


@pytest.mark.parametrize(
    ("role", "expected"),
    [(GlobalRole.ADMIN, 200), (GlobalRole.USER, 403), (GlobalRole.AUDITOR, 403)],
)
def test_only_admin_can_enqueue_recovery(role, expected):
    app, status, audit = application()
    principal = Principal(user_id="operator", global_role=role)
    app.state.principal = principal
    with TestClient(app) as client:
        response = client.post(
            "/admin/execution/recover",
            json={"scope_key": "user:target", "reason": "Fixed projection defect"},
        )
    assert response.status_code == expected
    assert status.request_recovery.await_count == int(expected == 200)
    assert audit.record.await_count == int(expected == 200)


def test_recovery_requires_explicit_reason_and_valid_scope():
    app, status, _ = application()
    principal = Principal(user_id="operator", global_role=GlobalRole.ADMIN)
    app.state.principal = principal
    with TestClient(app) as client:
        for body in (
            {"scope_key": "user:target", "reason": " "},
            {"scope_key": "invalid", "reason": "repair"},
        ):
            assert client.post("/admin/execution/recover", json=body).status_code in (400, 422)
    status.request_recovery.assert_not_awaited()
