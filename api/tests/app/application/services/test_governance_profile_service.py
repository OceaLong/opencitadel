from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.application.services.governance_profile_service import GovernanceProfileService
from app.domain.errors import NotFoundError
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session, SessionStatus


class _SessionRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def get_by_id(self, session_id: str, *, scope: OwnerScope):
        if session_id != self.session.id or scope.user_id != self.session.owner_user_id:
            return None
        return self.session


class _Uow:
    def __init__(self, session: Session, patrol_run_id: str | None = None) -> None:
        self.session = _SessionRepo(session)
        self.patrol = SimpleNamespace(
            get_run_by_session_id=self._get_patrol_run_by_session_id,
        )
        self._patrol_run_id = patrol_run_id

    async def _get_patrol_run_by_session_id(self, session_id: str):
        if self._patrol_run_id is None:
            return None
        return SimpleNamespace(id=self._patrol_run_id, session_id=session_id)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _RunProjection:
    def __init__(self) -> None:
        self.calls = []

    async def source_governance(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "chain": {"verified": True, "checked_runs": 1, "checked_entries": 4},
            "runs": [
                {
                    "run_id": "run-1",
                    "family": "agent",
                    "status": "completed",
                    "created_at": "2026-08-24T00:00:00+00:00",
                    "terminal_at": "2026-08-24T00:01:00+00:00",
                }
            ],
            "approvals": [
                {
                    "approval_id": "approval-1",
                    "status": "approved",
                    "decided_by_user_id": "user-1",
                }
            ],
            "activities": [
                {
                    "activity_id": "activity-1",
                    "activity_type": "tool.call",
                    "status": "completed",
                }
            ],
        }


@pytest.fixture
def environment():
    now = datetime(2026, 8, 24, tzinfo=UTC)
    session = Session(
        id="session-1",
        title="Operator run",
        owner_user_id="user-1",
        operator_scope="owned",
        operator_domains=["OPS-CONSOLE", "https://example.com/"],
        status=SessionStatus.COMPLETED,
        created_at=now,
        updated_at=now,
    )
    projection = _RunProjection()
    service = GovernanceProfileService(
        uow_factory=lambda: _Uow(session),
        run_projection=projection,
    )
    return service, projection


@pytest.mark.asyncio
async def test_profile_is_built_only_from_formal_execution_projection(environment):
    service, projection = environment
    scope = OwnerScope.personal("user-1")

    profile = await service.build_profile("session-1", scope)

    assert profile["session"] == {
        "id": "session-1",
        "title": "Operator run",
        "owner_user_id": "user-1",
        "team_id": None,
        "status": "completed",
        "operator_scope": "owned",
        "operator_domains": ["ops-console", "example.com"],
        "created_at": "2026-08-24T00:00:00+00:00",
        "updated_at": "2026-08-24T00:00:00+00:00",
    }
    assert profile["chain"]["verified"] is True
    assert profile["approvals"][0]["status"] == "approved"
    assert profile["activities"][0]["activity_type"] == "tool.call"
    assert projection.calls == [
        {
            "source_entity_type": "session",
            "source_entity_id": "session-1",
            "owner_scope": scope,
        }
    ]
    assert "gates" not in profile
    assert "checkpoints" not in profile
    assert "denials" not in profile


@pytest.mark.asyncio
async def test_profile_scope_mismatch_returns_not_found(environment):
    service, projection = environment

    with pytest.raises(NotFoundError):
        await service.build_profile("session-1", OwnerScope.personal("other-user"))

    assert projection.calls == []


@pytest.mark.asyncio
async def test_patrol_session_profile_follows_its_formal_patrol_run_source() -> None:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    session = Session(
        id="session-1",
        title="Patrol run",
        owner_user_id="user-1",
        team_id="team-1",
        status=SessionStatus.COMPLETED,
        created_at=now,
        updated_at=now,
    )
    projection = _RunProjection()
    service = GovernanceProfileService(
        uow_factory=lambda: _Uow(session, patrol_run_id="patrol-run-1"),
        run_projection=projection,
    )

    profile = await service.build_profile(
        "session-1",
        OwnerScope.team("user-1", "team-1"),
    )

    assert profile["session"] == {
        "id": "session-1",
        "title": "Patrol run",
        "owner_user_id": "user-1",
        "team_id": "team-1",
        "status": "completed",
        "operator_scope": None,
        "operator_domains": [],
        "created_at": "2026-08-24T00:00:00+00:00",
        "updated_at": "2026-08-24T00:00:00+00:00",
    }

    assert projection.calls == [
        {
            "source_entity_type": "patrol_run",
            "source_entity_id": "patrol-run-1",
            "owner_scope": OwnerScope.team("user-1", "team-1"),
        }
    ]
