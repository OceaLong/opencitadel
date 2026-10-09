from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.ports.execution_analysis import AnalysisCapture, AuthorityState
from app.composition.execution_analysis import build_analysis_service
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.interfaces.auth_dependencies import get_current_principal, get_workspace_context
from app.interfaces.endpoints.routes import create_api_routes
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_execution_analysis_service


@pytest.fixture
def client():
    principal = Principal(user_id="u")
    scope = OwnerScope.personal("u")
    repository = SimpleNamespace(
        capture=AsyncMock(
            return_value=AnalysisCapture(
                "opaque", AuthorityState(1, "private"), {"sample_count": 1}
            )
        ),
        current=AsyncMock(return_value=AuthorityState(1, "private")),
    )
    preference = SimpleNamespace(
        get=AsyncMock(return_value={"timezone": "Asia/Shanghai", "revision": 2})
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(create_api_routes(), prefix="/api")
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=principal, scope=scope
    )

    async def service():
        return await build_analysis_service(
            settings=None,
            resources=None,
            scope=scope,
            principal=principal,
            repository=repository,
            preferences=preference,
        )

    app.dependency_overrides[get_execution_analysis_service] = service
    with TestClient(app) as http:
        yield http, repository, preference, app


def test_summary_uses_real_scoped_preference_and_has_no_private_authority(client):
    http, repo, prefs, _ = client
    result = http.get(
        "/api/execution-analysis/summary", params={"timezone": "America/New_York", "grain": "hour"}
    )
    assert result.status_code == 200
    assert result.json()["data"]["timezone"] == "Asia/Shanghai"
    assert result.headers["cache-control"] == "no-store"
    assert "private" not in result.text
    assert repo.capture.await_args.args[2].timezone == "Asia/Shanghai"
    prefs.get.assert_awaited_once()


def test_summary_uses_requested_timezone_only_when_preference_absent(client):
    http, _, prefs, _ = client
    prefs.get.return_value = {"timezone": None, "revision": 0}
    result = http.get("/api/execution-analysis/summary", params={"timezone": "America/New_York"})
    assert result.status_code == 200
    assert result.json()["data"]["timezone"] == "America/New_York"


@pytest.mark.parametrize(
    "params",
    [
        {"secret": "x"},
        {"timezone": "not/a/zone"},
        {"grain": "week"},
        {"limit": "201"},
        {"filters": '{"private_payload":"x"}'},
    ],
)
def test_summary_rejects_unknown_or_invalid_query(client, params):
    assert client[0].get("/api/execution-analysis/summary", params=params).status_code == 400


def test_summary_revocation_during_compute_denies(client):
    http, repo, _, _ = client
    repo.current.return_value = AuthorityState(2, "private")
    assert http.get("/api/execution-analysis/summary").status_code == 403


def test_summary_requires_login(client):
    http, _, _, app = client
    app.dependency_overrides.pop(get_current_principal)
    assert http.get("/api/execution-analysis/summary").status_code == 401


def test_preference_mutation_forwards_explicit_cas_and_receipt(client):
    from app.interfaces.service_dependencies import get_analysis_preferences

    http, _, _, app = client
    preferences = SimpleNamespace(
        update=AsyncMock(return_value={"timezone": "Asia/Tokyo", "revision": 3})
    )
    app.dependency_overrides[get_analysis_preferences] = lambda: preferences
    result = http.put(
        "/api/execution-analysis/preferences",
        json={"timezone": "Asia/Tokyo", "expected_revision": 2, "request_id": "pref-1"},
    )
    assert result.status_code == 200
    assert result.json()["data"]["revision"] == 3
    assert preferences.update.await_args.kwargs == {
        "timezone": "Asia/Tokyo",
        "expected_revision": 2,
        "request_id": "pref-1",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"timezone": "Asia/Tokyo", "expected_revision": 2},
        {"timezone": "invalid", "expected_revision": 2, "request_id": "x"},
        {"timezone": None, "expected_revision": 2, "request_id": "x", "scope": "foreign"},
    ],
)
def test_preference_rejects_invalid_mutations(client, payload):
    from app.interfaces.service_dependencies import get_analysis_preferences

    http, _, _, app = client
    preferences = SimpleNamespace(update=AsyncMock())
    app.dependency_overrides[get_analysis_preferences] = lambda: preferences
    assert http.put("/api/execution-analysis/preferences", json=payload).status_code == 400
    preferences.update.assert_not_awaited()


@pytest.mark.parametrize(
    "reason",
    [
        "analysis_applicability_unavailable",
        "analysis_metric_version_unavailable",
        "analysis_refresh_required",
    ],
)
def test_unavailable_metric_semantics_returns_typed_unavailable(client, reason):
    http, repository, _, _ = client
    repository.capture.side_effect = ValueError(reason)
    response = http.get("/api/execution-analysis/summary")
    assert response.status_code == 409
    assert response.json()["data"]["code"] == "resource_unavailable"
