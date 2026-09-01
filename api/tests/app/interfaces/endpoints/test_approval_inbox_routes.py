"""Route-level wiring for GET /approvals (the reviewer inbox).

Mirrors test_governance_overview_routes.py: a bare FastAPI app with the real
router mounted and only the auth/service dependencies overridden.
"""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.ports.queries import ApprovalInboxEntry
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.domain.models.user import GlobalRole
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints import approval_routes
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_approval_inbox_service


class _FakeInboxService:
    def __init__(self, entries: list[ApprovalInboxEntry]) -> None:
        self._entries = entries
        self.calls: list[dict] = []

    async def list_approvals(self, *, owner_scope, status, limit, offset):
        self.calls.append(
            {
                "owner_scope": owner_scope,
                "status": status,
                "limit": limit,
                "offset": offset,
            }
        )
        return tuple(self._entries)


def _entry(subject_label: str) -> ApprovalInboxEntry:
    return ApprovalInboxEntry(
        approval_id=uuid4(),
        run_id=uuid4(),
        source_entity_type="session",
        source_entity_id="session-1",
        approval_kind="tool_effect",
        subject_activity_id=uuid4(),
        subject_label=subject_label,
        risk_summary="Write to an external system",
        status="pending",
        decision=None,
        decided_by_user_id=None,
        requested_at=datetime(2026, 9, 1, tzinfo=UTC),
        decided_at=None,
    )


def _app(service: _FakeInboxService) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(approval_routes.inbox_router)

    async def ctx() -> WorkspaceContext:
        return WorkspaceContext(
            principal=Principal(user_id="caller-1", global_role=GlobalRole.USER),
            scope=OwnerScope.personal("caller-1"),
        )

    app.dependency_overrides[get_workspace_context] = ctx
    app.dependency_overrides[get_approval_inbox_service] = lambda: service
    return app


def test_list_pending_approvals_passes_scope_status_and_paging() -> None:
    service = _FakeInboxService([_entry("write_external")])
    app = _app(service)

    with TestClient(app) as client:
        response = client.get("/approvals?status=pending&limit=10&offset=5")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["limit"] == 10
    assert body["offset"] == 5
    assert [item["subject_label"] for item in body["items"]] == ["write_external"]
    assert body["items"][0]["status"] == "pending"

    call = service.calls[0]
    assert call["status"] == "pending"
    assert call["limit"] == 10
    assert call["offset"] == 5
    assert call["owner_scope"].user_id == "caller-1"


def test_default_status_is_unfiltered() -> None:
    service = _FakeInboxService([])
    app = _app(service)

    with TestClient(app) as client:
        response = client.get("/approvals")

    assert response.status_code == 200
    assert service.calls[0]["status"] is None
    assert service.calls[0]["limit"] == 50
    assert service.calls[0]["offset"] == 0


def test_unknown_status_is_rejected() -> None:
    service = _FakeInboxService([])
    app = _app(service)

    with TestClient(app) as client:
        response = client.get("/approvals?status=bogus")

    assert response.status_code == 422
    assert service.calls == []
