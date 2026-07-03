#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.errors.exceptions import ServerRequestsError
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
async def test_list_revisions_rejects_unscoped_query():
    from app.infrastructure.repositories.db_app_config_repository import DbAppConfigRepository

    repo = DbAppConfigRepository()
    with pytest.raises(ServerRequestsError):
        await repo.list_revisions()
