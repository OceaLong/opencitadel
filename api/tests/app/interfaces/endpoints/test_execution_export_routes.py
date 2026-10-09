import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.interfaces.auth_dependencies import get_current_principal, get_workspace_context
from app.interfaces.endpoints.routes import create_api_routes
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_execution_export_service


@pytest.fixture
def client():
    principal = Principal(user_id="u")
    scope = OwnerScope.personal("u")
    identity = str(uuid4())
    service = SimpleNamespace(
        create=AsyncMock(return_value={"id": identity, "status": "queued"}),
        get=AsyncMock(return_value={"id": identity, "status": "expired"}),
        download=AsyncMock(),
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(create_api_routes(), prefix="/api")
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=principal, scope=scope
    )
    app.dependency_overrides[get_execution_export_service] = lambda: service
    with TestClient(app) as http:
        yield http, service, identity


def test_all_exports_are_async_and_no_store(client):
    from app.interfaces.schemas.execution_export import ExportJob

    http, _service, identity = client
    response = http.post(
        "/api/execution-analysis/exports",
        json={
            "source_kind": "comparison",
            "format": "json",
            "request_id": "r",
            "comparison_id": str(uuid4()),
            "revision": 1,
        },
    )
    assert response.status_code == 202
    assert response.json()["code"] == response.status_code
    assert response.headers["cache-control"] == "no-store"
    job = ExportJob.model_validate(response.json()["data"])
    assert str(job.id) == identity
    assert job.status == "queued"


def test_missing_fixed_revision_and_private_capture_inputs_rejected(client):
    http, service, _identity = client
    for payload in (
        {
            "source_kind": "comparison",
            "format": "json",
            "request_id": "r",
            "comparison_id": str(uuid4()),
        },
        {
            "source_kind": "filter",
            "format": "csv",
            "request_id": "r",
            "selection": {"mode": "all_matching"},
            "capture": {"secret": "x"},
        },
    ):
        assert http.post("/api/execution-analysis/exports", json=payload).status_code == 400
    service.create.assert_not_awaited()


def test_expired_status_has_no_original_counts(client):
    http, _service, identity = client
    response = http.get(f"/api/execution-analysis/exports/{identity}")
    assert response.status_code == 200
    assert response.json()["data"] == {"id": identity, "status": "expired"}


def test_content_is_authenticated_bytes_and_spool_closes(client):
    http, service, identity = client
    spool = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115 - route owns close
    spool.write(b"fixed")
    spool.seek(0)
    service.download.return_value = (spool, "csv", 5)
    response = http.get(f"/api/execution-analysis/exports/{identity}/content")
    assert response.status_code == 200
    assert response.content == b"fixed"
    assert response.headers["cache-control"] == "no-store"
    assert "location" not in response.headers
    assert spool.closed


def test_scope_not_found_and_revoked_download_denied(client):
    http, service, identity = client
    service.get.side_effect = ValueError("export_not_found")
    assert http.get(f"/api/execution-analysis/exports/{identity}").status_code == 404
    service.download.side_effect = PermissionError("revoked")
    response = http.get(f"/api/execution-analysis/exports/{identity}/content")
    assert response.status_code == 403
    assert "fixed" not in response.text


def test_content_expiry_has_explicit_status_and_capacity_has_stable_code(client):
    http, service, identity = client
    service.download.side_effect = ValueError("export_expired")
    response = http.get(f"/api/execution-analysis/exports/{identity}/content")
    assert response.status_code == 410
    assert response.json()["data"]["code"] == "export_expired"
    service.create.side_effect = ValueError("export_capacity_exceeded")
    response = http.post(
        "/api/execution-analysis/exports",
        json={
            "source_kind": "comparison",
            "format": "json",
            "request_id": "r",
            "comparison_id": str(uuid4()),
            "revision": 1,
        },
    )
    assert response.status_code == 413
    assert response.json()["data"]["code"] == "export_capacity_exceeded"


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        ("export_expired", 410),
        ("export_capacity_exceeded", 413),
        ("export_quota_exceeded", 429),
        ("export_not_found", 404),
    ],
)
def test_export_error_bodies_match_declared_contract(client, reason, status):
    from app.interfaces.schemas.execution_export import ExportError

    http, service, identity = client
    service.get.side_effect = ValueError(reason)
    response = http.get(f"/api/execution-analysis/exports/{identity}")
    assert response.status_code == status
    assert ExportError.model_validate(response.json()["data"]).code == reason
    operation = http.app.openapi()["paths"]["/api/execution-analysis/exports/{export_id}"]["get"]
    assert str(status) in operation["responses"]
    assert "ExportError" in str(operation["responses"][str(status)])
