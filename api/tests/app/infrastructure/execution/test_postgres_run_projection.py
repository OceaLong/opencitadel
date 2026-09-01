from unittest.mock import AsyncMock

import pytest

from app.infrastructure.execution.postgres_run_projection import PostgresRunProjection


class _ScalarRows:
    def all(self) -> list[object]:
        return []


class _Session:
    def __init__(self) -> None:
        self.statements: list[object] = []

    async def scalars(self, statement: object) -> _ScalarRows:
        self.statements.append(statement)
        return _ScalarRows()


class _SessionContext:
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def __aenter__(self) -> _Session:
        return self._session

    async def __aexit__(self, *args: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_source_governance_allows_auditor_cross_owner_query(monkeypatch) -> None:
    session = _Session()
    configure_authorization = AsyncMock()
    monkeypatch.setattr(
        "app.infrastructure.execution.postgres_run_projection.configure_session_authorization",
        configure_authorization,
    )
    projection = PostgresRunProjection(
        session_factory=lambda: _SessionContext(session),  # type: ignore[arg-type]
        authorization=None,
    )

    result = await projection.source_governance(
        source_entity_type="session",
        source_entity_id="session-1",
        owner_scope=None,
    )

    assert result == {
        "chain": {"verified": True, "checked_runs": 0, "checked_entries": 0},
        "runs": [],
        "approvals": [],
        "activities": [],
    }
    assert len(session.statements) == 1
    configure_authorization.assert_awaited_once_with(session, None)
