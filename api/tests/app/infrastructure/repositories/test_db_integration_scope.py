import pytest

from app.domain.models.scope import OwnerScope
from app.infrastructure.repositories.db_integration_server_repository import (
    DBA2AServerRepository,
    DBMCPServerRepository,
)


class _FakeScalars:
    def all(self):
        return []


class _FakeResult:
    def __init__(self):
        pass

    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return _FakeScalars()


class _FakeSession:
    def __init__(self, captured_stmt):
        self.captured_stmt = captured_stmt

    async def execute(self, stmt):
        self.captured_stmt["stmt"] = stmt
        return _FakeResult()


class _FakeCipher:
    pass


@pytest.mark.asyncio
async def test_mcp_apply_scope_none_filters_global_only():
    captured: dict = {}
    repo = DBMCPServerRepository(_FakeSession(captured), _FakeCipher())
    await repo.list_all(scope=None)
    stmt = captured["stmt"]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "visibility" in compiled
    assert "global" in compiled


@pytest.mark.asyncio
async def test_a2a_apply_scope_none_filters_global_only():
    captured: dict = {}
    repo = DBA2AServerRepository(_FakeSession(captured))
    await repo.list_all(scope=None)
    stmt = captured["stmt"]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "visibility" in compiled
    assert "global" in compiled


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repo_factory", "table_name"),
    [
        (
            lambda captured: DBMCPServerRepository(_FakeSession(captured), _FakeCipher()),
            "mcp_servers",
        ),
        (lambda captured: DBA2AServerRepository(_FakeSession(captured)), "a2a_servers"),
    ],
)
async def test_team_scope_filters_integration_servers_by_team_not_creator(
    repo_factory,
    table_name,
):
    captured: dict = {}
    repo = repo_factory(captured)

    await repo.list_all(scope=OwnerScope.team("member-2", "team-1"))

    compiled = captured["stmt"].compile()
    where_sql = str(compiled).split("WHERE", 1)[1]
    assert f"{table_name}.team_id" in where_sql
    assert f"{table_name}.owner_user_id" not in where_sql
    assert "team-1" in compiled.params.values()
    assert "member-2" not in compiled.params.values()
