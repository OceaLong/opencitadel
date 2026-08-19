#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Route-level RBAC + wiring coverage for GET /admin/governance/overview.

App-building pattern mirrors test_compliance_routes.py: a bare FastAPI app
with the real router mounted and only the auth/service dependencies
overridden, exercised through TestClient.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.errors import ForbiddenError
from app.domain.models.scope import Principal, WorkspaceContext, OwnerScope
from app.domain.models.user import GlobalRole
from app.interfaces.auth_dependencies import get_workspace_context, require_auditor_or_admin
from app.interfaces.endpoints import compliance_routes
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_governance_overview_service


class _FakeGovernanceOverviewService:
    def __init__(self):
        self.calls: list[int] = []

    async def build_overview(self, *, days: int = 30):
        self.calls.append(days)
        return {
            "approvals": {
                "pending_count": 2,
                "avg_decision_seconds": 12.5,
                "outcomes": {"approved": 3, "rejected": 1, "expired": 0, "consumed": 3},
            },
            "interceptions": [{"date": "2026-08-01", "approval_decisions": 2, "denials": 1}],
            "patrol": [{"date": "2026-08-01", "runs": 1, "findings": 1}],
            "remediation": {
                "by_status": {
                    "proposed": 0,
                    "executing": 0,
                    "executed": 1,
                    "verified": 2,
                    "failed": 0,
                    "cancelled": 0,
                },
                "success_rate": 2 / 3,
            },
            "chain": {
                "ok": True,
                "total": 100,
                "first_broken_seq": None,
                "checked_at": "2026-08-13T00:00:00Z",
            },
        }


async def _deny_auditor_gate():
    raise ForbiddenError("需要管理员或审计员权限")


def _app(service, *, role: GlobalRole, allow: bool) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(compliance_routes.router)

    async def ctx() -> WorkspaceContext:
        principal = Principal(user_id="caller-1", global_role=role)
        return WorkspaceContext(
            principal=principal,
            scope=OwnerScope.team("caller-1", "team-1"),
        )

    async def allow_gate() -> Principal:
        return Principal(user_id="caller-1", global_role=role)

    app.dependency_overrides[get_workspace_context] = ctx
    app.dependency_overrides[require_auditor_or_admin] = (
        allow_gate if allow else _deny_auditor_gate
    )
    app.dependency_overrides[get_governance_overview_service] = lambda: service
    return app


def test_auditor_can_get_governance_overview():
    service = _FakeGovernanceOverviewService()
    app = _app(service, role=GlobalRole.AUDITOR, allow=True)

    with TestClient(app) as client:
        response = client.get("/admin/governance/overview")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["approvals"]["pending_count"] == 2
    assert body["interceptions"] == [{"date": "2026-08-01", "approval_decisions": 2, "denials": 1}]
    assert body["patrol"] == [{"date": "2026-08-01", "runs": 1, "findings": 1}]
    assert body["remediation"]["success_rate"] == 2 / 3
    assert body["chain"]["ok"] is True
    # default days=30 flows through to the service
    assert service.calls == [30]


def test_admin_can_get_governance_overview_with_custom_days():
    service = _FakeGovernanceOverviewService()
    app = _app(service, role=GlobalRole.ADMIN, allow=True)

    with TestClient(app) as client:
        response = client.get("/admin/governance/overview?days=7")

    assert response.status_code == 200
    assert service.calls == [7]


def test_non_admin_user_is_forbidden():
    service = _FakeGovernanceOverviewService()
    app = _app(service, role=GlobalRole.USER, allow=False)

    with TestClient(app) as client:
        response = client.get("/admin/governance/overview")

    assert response.status_code == 403
    assert service.calls == []


def test_days_out_of_range_is_rejected():
    service = _FakeGovernanceOverviewService()
    app = _app(service, role=GlobalRole.ADMIN, allow=True)

    with TestClient(app) as client:
        response = client.get("/admin/governance/overview?days=0")

    assert response.status_code == 422
    assert service.calls == []
