from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.ports.execution_view import ViewNotFound, ViewRebuilding, ViewRevisionExpired
from app.application.services.execution_view_service import ExecutionViewService
from app.domain.models.playback import PlaybackBoundary
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.interfaces.auth_dependencies import get_current_principal, get_workspace_context
from app.interfaces.endpoints.routes import create_api_routes
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_execution_view_service


class HeadPort:
    failure = None

    @asynccontextmanager
    async def transaction(self, **kwargs):
        yield None

    async def capture_run(self, *args):
        raise self.failure or ViewNotFound()


@pytest.fixture
def client():
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(create_api_routes(), prefix="/api")
    port = HeadPort()
    service = ExecutionViewService(port, cursor_secret=b"0123456789abcdef")
    principal = Principal(user_id="u")
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=principal, scope=OwnerScope.personal("u")
    )
    app.dependency_overrides[get_execution_view_service] = lambda: service
    with TestClient(app) as client:
        yield client, app, port, service


def test_not_logged_in_is_denied(client):
    http, app, _, _ = client
    app.dependency_overrides.pop(get_current_principal)
    assert http.get("/api/execution-runs").status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/execution-runs?limit=201",
        "/execution-runs?limit=0",
        "/execution-runs?limit=wrong",
        "/execution-runs?cursor=foreign",
        f"/execution-runs/{uuid4()}/steps?limit=501",
    ],
)
def test_invalid_queries_have_stable_400(client, path):
    http, *_ = client
    result = http.get("/api" + path)
    assert result.status_code == 400
    assert result.json()["data"]["code"] == "invalid_argument"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ViewNotFound(), 404, "not_found"),
        (ViewRebuilding(), 503, "projection_rebuilding"),
        (ViewRevisionExpired(), 409, "revision_conflict"),
    ],
)
def test_service_failures_are_stable_and_do_not_leak_private_messages(client, error, status, code):
    http, _, port, _ = client
    port.failure = error
    result = http.get(f"/api/execution-runs/{uuid4()}/view")
    assert result.status_code == status
    assert result.json()["data"]["code"] == code


def test_exact_revision_disagreement_is_conflict(client):
    http, _, _, service = client
    run = uuid4()
    boundary = PlaybackBoundary(
        run_id=run,
        formal_position=1,
        progress_position=0,
        observed_order=1,
        projection_revision=1,
        observed_at=datetime.now(UTC),
        projector_version=1,
    )
    at = service._at(OwnerScope.personal("u"), boundary, "live")
    result = http.get(f"/api/execution-runs/{run}/steps", params={"at": at, "revision": 2})
    assert result.status_code == 409
    assert result.json()["data"]["code"] == "revision_conflict"
