"""Route wiring for the ownership-safe deletion strategies (E1 + E2).

Bare-app pattern mirrors test_governance_overview_routes.py: mount the real
routers, override the admin gate + service/uow dependencies with fakes, and
drive through TestClient. Focus is on parameter acceptance, service wiring and
audit emission — the resource-reassignment SQL itself is proven against real
PostgreSQL in test_db_ownership_transfer_postgres.py.
"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.security.authorization_context import set_authorization_context
from app.application.services.team_service import TeamDeletionResult
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import Principal
from app.domain.models.user import GlobalRole, UserStatus
from app.interfaces.auth_context import set_principal
from app.interfaces.auth_dependencies import get_current_principal, require_admin
from app.interfaces.endpoints import admin_routes, team_routes
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import (
    get_audit_service,
    get_team_service,
    get_uow_factory,
)


class _FakeAuditService:
    def __init__(self) -> None:
        self.records: list = []

    async def record(self, log) -> None:
        self.records.append(log)


class _FakeUser:
    def __init__(self, user_id: str) -> None:
        self.id = user_id
        self.status = UserStatus.ACTIVE
        self.token_version = 0
        self.email = f"{user_id}@test.local"
        self.username = user_id
        self.display_name = user_id


class _FakeUserRepo:
    def __init__(self, user: _FakeUser | None) -> None:
        self._user = user
        self.transfers: list[tuple[str, str]] = []
        self.saved: list[_FakeUser] = []
        self.security_revoked: list[str] = []

    async def get_by_id(self, user_id: str):
        return self._user

    async def transfer_personal_resources_to_team(self, user_id: str, team_id: str) -> int:
        self.transfers.append((user_id, team_id))
        return 4

    async def save(self, user) -> None:
        self.saved.append(user)

    async def revoke_security_material(self, user_id: str) -> None:
        self.security_revoked.append(user_id)


class _FakeTeamRepo:
    def __init__(self, team_ids: set[str]) -> None:
        self._team_ids = team_ids

    async def get_by_id(self, team_id: str):
        if team_id not in self._team_ids:
            return None
        return SimpleNamespace(id=team_id)


class _FakeRefreshTokenRepo:
    def __init__(self) -> None:
        self.revoked: list[str] = []

    async def revoke_all_for_user(self, user_id: str) -> None:
        self.revoked.append(user_id)


class _FakeUow:
    def __init__(self, user_repo, team_repo, refresh_repo) -> None:
        self.user = user_repo
        self.team = team_repo
        self.refresh_token = refresh_repo
        self.committed = False
        self.authorization_context = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self.committed = True


class _FakeUowFactory:
    def __init__(self, uow) -> None:
        self._uow = uow
        self.contexts: list = []

    def __call__(self, authorization_context=None):
        self.contexts.append(authorization_context)
        self._uow.authorization_context = authorization_context
        return self._uow


class _FakeTeamService:
    def __init__(self, result: TeamDeletionResult) -> None:
        self._result = result
        self.delete_calls: list[dict] = []
        self.admin_delete_calls: list[dict] = []

    async def delete_team(self, *, team_id: str, actor_user_id: str, strategy: str):
        self.delete_calls.append(
            {"team_id": team_id, "actor_user_id": actor_user_id, "strategy": strategy}
        )
        return self._result

    async def admin_delete_team(self, team_id: str, *, strategy: str):
        self.admin_delete_calls.append({"team_id": team_id, "strategy": strategy})
        return self._result


def _app(
    *,
    uow_factory=None,
    team_service=None,
    audit_service=None,
    principal_id: str = "admin-1",
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(admin_routes.router)
    app.include_router(team_routes.router)

    principal = Principal(user_id=principal_id, global_role=GlobalRole.ADMIN)

    async def _gate():
        # Bind the contextvar so endpoints that call get_current_principal()
        # directly (e.g. delete_user) resolve the admin identity.
        set_principal(principal)
        set_authorization_context(AuthorizationContext.for_principal(principal))
        return principal

    app.dependency_overrides[require_admin] = _gate
    app.dependency_overrides[get_current_principal] = _gate
    if uow_factory is not None:
        app.dependency_overrides[get_uow_factory] = lambda: uow_factory
    if team_service is not None:
        app.dependency_overrides[get_team_service] = lambda: team_service
    if audit_service is not None:
        app.dependency_overrides[get_audit_service] = lambda: audit_service
    return app


# ---------------------------------------------------------------------------
# E1: admin delete_user — transfer_to_team
# ---------------------------------------------------------------------------


def test_delete_user_transfer_to_team_moves_resources_and_audits():
    user_repo = _FakeUserRepo(_FakeUser("victim-1"))
    uow = _FakeUow(user_repo, _FakeTeamRepo({"team-9"}), _FakeRefreshTokenRepo())
    factory = _FakeUowFactory(uow)
    audit = _FakeAuditService()
    app = _app(uow_factory=factory, audit_service=audit)

    with TestClient(app) as client:
        response = client.request(
            "DELETE", "/admin/users/victim-1?strategy=transfer_to_team&team_id=team-9"
        )

    assert response.status_code == 200
    assert response.json()["data"]["strategy"] == "transfer_to_team"
    assert user_repo.transfers == [("victim-1", "team-9")]
    # user is disabled + anonymized, security material revoked.
    assert len(user_repo.saved) == 1
    assert user_repo.saved[0].status == UserStatus.DISABLED
    assert user_repo.security_revoked == ["victim-1"]
    assert uow.committed is True
    # cross-user reassignment runs under a system scope.
    assert len(factory.contexts) == 1
    assert factory.contexts[0] is not None
    assert factory.contexts[0].mode.value == "system"
    # audit captures strategy + target team + moved count.
    assert len(audit.records) == 1
    meta = audit.records[0].metadata
    assert meta["strategy"] == "transfer_to_team"
    assert meta["team_id"] == "team-9"
    assert meta["moved_resources"] == 4


def test_delete_user_transfer_to_team_requires_team_id():
    user_repo = _FakeUserRepo(_FakeUser("victim-1"))
    uow = _FakeUow(user_repo, _FakeTeamRepo({"team-9"}), _FakeRefreshTokenRepo())
    factory = _FakeUowFactory(uow)
    audit = _FakeAuditService()
    app = _app(uow_factory=factory, audit_service=audit)

    with TestClient(app) as client:
        response = client.request("DELETE", "/admin/users/victim-1?strategy=transfer_to_team")

    assert response.status_code == 400
    assert user_repo.transfers == []
    assert audit.records == []


def test_delete_user_transfer_to_team_missing_team_is_not_found():
    user_repo = _FakeUserRepo(_FakeUser("victim-1"))
    uow = _FakeUow(user_repo, _FakeTeamRepo(set()), _FakeRefreshTokenRepo())
    factory = _FakeUowFactory(uow)
    audit = _FakeAuditService()
    app = _app(uow_factory=factory, audit_service=audit)

    with TestClient(app) as client:
        response = client.request(
            "DELETE", "/admin/users/victim-1?strategy=transfer_to_team&team_id=ghost"
        )

    assert response.status_code == 404
    assert user_repo.transfers == []


def test_delete_user_rejects_unknown_strategy():
    app = _app(
        uow_factory=_FakeUowFactory(_FakeUow(_FakeUserRepo(None), None, None)),
        audit_service=_FakeAuditService(),
    )

    with TestClient(app) as client:
        response = client.request("DELETE", "/admin/users/victim-1?strategy=teleport")

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# E2: team delete_team — strategy + audit
# ---------------------------------------------------------------------------


def test_team_delete_defaults_to_transfer_to_owner_and_audits():
    result = TeamDeletionResult(
        strategy="transfer_to_owner", affected_resources=3, transferred_to_user_id="admin-1"
    )
    service = _FakeTeamService(result)
    audit = _FakeAuditService()
    app = _app(team_service=service, audit_service=audit)

    with TestClient(app) as client:
        response = client.request("DELETE", "/teams/team-1")

    assert response.status_code == 200
    assert service.delete_calls == [
        {"team_id": "team-1", "actor_user_id": "admin-1", "strategy": "transfer_to_owner"}
    ]
    assert len(audit.records) == 1
    meta = audit.records[0].metadata
    assert audit.records[0].action == "team.delete"
    assert meta["strategy"] == "transfer_to_owner"
    assert meta["affected_resources"] == 3
    assert meta["transferred_to_user_id"] == "admin-1"


def test_team_delete_accepts_cascade_strategy():
    result = TeamDeletionResult(
        strategy="cascade", affected_resources=8, transferred_to_user_id=None
    )
    service = _FakeTeamService(result)
    audit = _FakeAuditService()
    app = _app(team_service=service, audit_service=audit)

    with TestClient(app) as client:
        response = client.request("DELETE", "/teams/team-1?strategy=cascade")

    assert response.status_code == 200
    assert service.delete_calls[0]["strategy"] == "cascade"
    assert audit.records[0].metadata["strategy"] == "cascade"


def test_team_delete_rejects_unknown_strategy():
    service = _FakeTeamService(
        TeamDeletionResult(strategy="cascade", affected_resources=0, transferred_to_user_id=None)
    )
    app = _app(team_service=service, audit_service=_FakeAuditService())

    with TestClient(app) as client:
        response = client.request("DELETE", "/teams/team-1?strategy=archive")

    assert response.status_code == 422
    assert service.delete_calls == []


def test_admin_team_delete_passes_strategy_and_audits_counts():
    result = TeamDeletionResult(
        strategy="cascade", affected_resources=5, transferred_to_user_id=None
    )
    service = _FakeTeamService(result)
    audit = _FakeAuditService()
    app = _app(team_service=service, audit_service=audit)

    with TestClient(app) as client:
        response = client.request("DELETE", "/admin/teams/team-1?strategy=cascade")

    assert response.status_code == 200
    assert service.admin_delete_calls == [{"team_id": "team-1", "strategy": "cascade"}]
    meta = audit.records[0].metadata
    assert audit.records[0].action == "admin.team.delete"
    assert meta["strategy"] == "cascade"
    assert meta["affected_resources"] == 5
