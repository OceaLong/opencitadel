#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Route-level RBAC coverage for the auditor governance profile API.

App-building pattern mirrors
tests/app/interfaces/endpoints/test_resource_build_routes.py: a bare
FastAPI app with the real router mounted and only the auth/service
dependencies overridden, exercised through TestClient.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.errors import ForbiddenError, NotFoundError
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.domain.models.user import GlobalRole
from app.interfaces.auth_dependencies import get_workspace_context, require_auditor_or_admin
from app.interfaces.endpoints import compliance_routes
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_governance_profile_service


class _FakeGovernanceProfileService:
    """Scope-aware stub: only 'session-1' inside team-1 resolves."""

    def __init__(self):
        self.calls: list[tuple[str, OwnerScope]] = []

    async def build_profile(self, session_id: str, scope: OwnerScope):
        self.calls.append((session_id, scope))
        if session_id != "session-1" or scope.team_id != "team-1":
            raise NotFoundError("session not found")
        return {
            "session": {"id": session_id, "status": "completed"},
            "chain": {"verified": True, "checked_entries": 2},
            "approvals": [],
            "gate_hits": [],
            "checkpoints": [],
            "terminal": {"status": "completed", "reached_at": "2026-08-04T00:00:00Z"},
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
    app.dependency_overrides[get_governance_profile_service] = lambda: service
    return app


def test_auditor_can_get_governance_profile():
    service = _FakeGovernanceProfileService()
    app = _app(service, role=GlobalRole.AUDITOR, allow=True)

    with TestClient(app) as client:
        response = client.get("/admin/governance/sessions/session-1/profile")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["session"]["id"] == "session-1"
    assert body["data"]["chain"]["verified"] is True


def test_non_admin_user_is_forbidden():
    service = _FakeGovernanceProfileService()
    app = _app(service, role=GlobalRole.USER, allow=False)

    with TestClient(app) as client:
        response = client.get("/admin/governance/sessions/session-1/profile")

    assert response.status_code == 403
    assert service.calls == []


def test_cross_tenant_session_returns_not_found():
    service = _FakeGovernanceProfileService()
    app = _app(service, role=GlobalRole.ADMIN, allow=True)

    with TestClient(app) as client:
        response = client.get("/admin/governance/sessions/other-team-session/profile")

    assert response.status_code == 404
