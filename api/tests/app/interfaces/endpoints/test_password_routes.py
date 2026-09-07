from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.ports.crypto import CSRF_COOKIE, CSRF_HEADER
from app.domain.models.scope import Principal
from app.domain.models.user import GlobalRole
from app.infrastructure.security.csrf import CsrfService
from app.interfaces.endpoints import admin_routes, auth_routes
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_auth_service, get_csrf_service


def test_reset_password_rejects_non_admin_and_missing_csrf(monkeypatch):
    principal = Principal(user_id="reader")
    monkeypatch.setattr("app.interfaces.auth_dependencies.get_principal", lambda: principal)
    app = FastAPI()
    app.include_router(admin_routes.router)
    register_exception_handlers(app)
    service = SimpleNamespace(reset_password=AsyncMock())
    app.dependency_overrides[get_auth_service] = lambda: service
    app.dependency_overrides[get_csrf_service] = lambda: CsrfService()
    with TestClient(app) as client:
        body = {"new_password": "new-password"}
        assert client.post("/admin/users/alice/password", json=body).status_code == 403
        client.cookies.set(CSRF_COOKIE, "csrf-test")
        headers = {CSRF_HEADER: "csrf-test"}
        assert (
            client.post("/admin/users/alice/password", json=body, headers=headers).status_code
            == 403
        )
        service.reset_password.assert_not_awaited()
        principal = Principal(user_id="admin", global_role=GlobalRole.ADMIN)
        assert (
            client.post("/admin/users/alice/password", json=body, headers=headers).status_code
            == 200
        )
        assert service.reset_password.call_args.kwargs["user_id"] == "alice"
        assert service.reset_password.call_args.kwargs["principal"].user_id == "admin"


def test_self_password_change_rejects_missing_csrf(monkeypatch):
    from app.interfaces.service_dependencies import get_cookie_manager

    monkeypatch.setattr(
        "app.interfaces.auth_dependencies.get_principal", lambda: Principal(user_id="alice")
    )
    app = FastAPI()
    app.include_router(auth_routes.router)
    register_exception_handlers(app)
    service = SimpleNamespace(change_password=AsyncMock())
    app.dependency_overrides[get_auth_service] = lambda: service
    app.dependency_overrides[get_csrf_service] = lambda: CsrfService()
    app.dependency_overrides[get_cookie_manager] = lambda: SimpleNamespace()
    with TestClient(app) as client:
        assert (
            client.post(
                "/auth/password",
                json={"current_password": "old-password", "new_password": "new-password"},
            ).status_code
            == 403
        )
        service.change_password.assert_not_awaited()
