from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.errors import ConflictError, NotFoundError
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.domain.models.user import GlobalRole
from app.interfaces.auth_dependencies import get_current_principal, get_workspace_context
from app.interfaces.endpoints.execution_view_routes import router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_agent_service


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    register_exception_handlers(app)
    principal = Principal(user_id="owner")
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=principal, scope=OwnerScope.personal("owner")
    )
    service = AsyncMock()
    result = {"command_id": uuid4(), "status": "pending"}
    service.refresh_tool_intent.return_value = result
    service.tool_intent_refresh_status.return_value = result
    app.dependency_overrides[get_agent_service] = lambda: service
    run_id, activity_id = uuid4(), uuid4()
    path = f"/api/execution-runs/{run_id}/tool-intents/{activity_id}/refresh"
    with TestClient(app) as http:
        yield http, service, principal, path, result


def body():
    return {"expected_subject_digest": "a" * 64, "reason": "Reviewed changed policy"}


def test_refresh_enqueues_exact_intent_and_exposes_durable_status(client):
    http, service, principal, path, result = client
    response = http.post(path, json=body(), headers={"Idempotency-Key": "refresh-1"})
    assert response.status_code == 202
    assert response.json()["data"]["command_id"] == str(result["command_id"])
    assert response.headers["cache-control"] == "no-store"
    args = service.refresh_tool_intent.call_args.kwargs
    assert args["principal"] == principal
    assert args["owner_scope"] == OwnerScope.personal("owner")
    assert args["expected_subject_digest"] == "a" * 64
    assert args["idempotency_key"] == "refresh-1"
    response = http.get(f"{path}/{result['command_id']}")
    assert response.status_code == 200
    assert service.tool_intent_refresh_status.call_args.kwargs["owner_scope"] == args["owner_scope"]


@pytest.mark.parametrize("key", [None, "", "   "])
def test_refresh_requires_idempotency_key(client, key):
    http, service, _, path, _ = client
    assert (
        http.post(
            path, json=body(), headers={} if key is None else {"Idempotency-Key": key}
        ).status_code
        == 400
    )
    service.refresh_tool_intent.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"reason": " "},
        {"expected_subject_digest": "wrong"},
        {"arguments": {"target": "new"}},
        {"principal": "admin"},
    ],
)
def test_cannot_rewrite_arguments_authority_or_skip_exact_subject(client, change):
    http, service, _, path, _ = client
    assert (
        http.post(path, json=body() | change, headers={"Idempotency-Key": "refresh-1"}).status_code
        == 400
    )
    service.refresh_tool_intent.assert_not_called()


def test_auditor_has_no_refresh_authority_even_without_outer_router(client):
    http, service, principal, path, _ = client
    principal.global_role = GlobalRole.AUDITOR
    assert http.post(path, json=body(), headers={"Idempotency-Key": "refresh-1"}).status_code == 403
    service.refresh_tool_intent.assert_not_called()


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ConflictError("private"), 409),
        (NotFoundError("private"), 404),
        (PermissionError("private"), 403),
    ],
)
def test_scoped_rejections_do_not_leak_private_intent(client, error, status):
    http, service, _, path, _ = client
    service.refresh_tool_intent.side_effect = error
    response = http.post(path, json=body(), headers={"Idempotency-Key": "refresh-1"})
    assert response.status_code == status
    assert "private" not in response.text
