"""Route-level contract for the A2A JSON-RPC facade.

Covers the JSON-RPC method dispatch table: the newly added ``tasks/get`` /
``tasks/cancel`` methods reach the service, and unknown methods return the
protocol-standard ``-32601`` error with an English message (A2A is an outward
facing protocol).

These build a minimal app containing only the A2A router so the assertions do
not depend on the full application lifespan (which requires a seeded runtime
policy in the database).
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.models.scope import Principal
from app.domain.models.user import GlobalRole
from app.interfaces.auth_dependencies import require_service_api_key
from app.interfaces.endpoints.a2a_routes import a2a_router
from app.interfaces.service_dependencies import get_a2a_server_service


@pytest.fixture
def a2a_client():
    service = AsyncMock()
    app = FastAPI()
    app.include_router(a2a_router, prefix="/api/a2a")
    app.dependency_overrides[require_service_api_key] = lambda: Principal(
        user_id="owner-1", global_role=GlobalRole.USER
    )
    app.dependency_overrides[get_a2a_server_service] = lambda: service
    with TestClient(app) as client:
        yield client, service


def test_unknown_method_returns_english_method_not_found(a2a_client):
    client, _service = a2a_client

    response = client.post(
        "/api/a2a",
        json={"jsonrpc": "2.0", "id": "req-1", "method": "does/notexist"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "req-1"
    assert body["error"]["code"] == -32601
    assert body["error"]["message"] == "Method not found: does/notexist"


def test_tasks_get_is_dispatched_to_service(a2a_client):
    client, service = a2a_client
    service.handle_task_get.return_value = {
        "jsonrpc": "2.0",
        "id": "req-2",
        "result": {"id": "session-1", "kind": "task", "status": {"state": "working"}},
    }

    response = client.post(
        "/api/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "req-2",
            "method": "tasks/get",
            "params": {"id": "session-1"},
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["status"]["state"] == "working"
    service.handle_task_get.assert_awaited_once()


def test_tasks_cancel_is_dispatched_to_service(a2a_client):
    client, service = a2a_client
    service.handle_task_cancel.return_value = {
        "jsonrpc": "2.0",
        "id": "req-3",
        "result": {"id": "session-1", "kind": "task", "status": {"state": "canceled"}},
    }

    response = client.post(
        "/api/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "req-3",
            "method": "tasks/cancel",
            "params": {"id": "session-1"},
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["status"]["state"] == "canceled"
    service.handle_task_cancel.assert_awaited_once()
