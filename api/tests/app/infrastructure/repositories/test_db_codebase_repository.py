#!/usr/bin/env python
# -*- coding: utf-8 -*-
from types import SimpleNamespace

import pytest

from app.infrastructure.repositories.db_codebase_repository import DBCodebaseRepository


class _Result:
    def fetchall(self):
        return []


class _Session:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return _Result()


@pytest.mark.asyncio
async def test_search_lexical_uses_generated_search_vector_and_exact_version():
    session = _Session()
    repo = DBCodebaseRepository(session)

    assert await repo.search_lexical("cb1", "cbv1", "create user", limit=5) == []

    sql, params = session.calls[-1]
    assert "search_vector @@ plainto_tsquery('simple', :query)" in sql
    assert "version_id = :version_id" in sql
    assert params == {
        "codebase_id": "cb1",
        "version_id": "cbv1",
        "query": "create user",
        "limit": 5,
    }


@pytest.mark.asyncio
async def test_search_vector_uses_exact_version_and_pgvector_ordering():
    session = _Session()
    repo = DBCodebaseRepository(session)

    assert await repo.search_vector("cb1", "cbv1", [0.1, 0.2], limit=7) == []

    sql, params = session.calls[-1]
    assert "embedding <=> :query::vector" in sql
    assert "version_id = :version_id" in sql
    assert params == {
        "codebase_id": "cb1",
        "version_id": "cbv1",
        "query": "[0.1, 0.2]",
        "limit": 7,
    }
