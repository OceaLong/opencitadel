#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for DBMemoryEntryRepository's pgvector-backed methods.

pgvector's `<=>` operator has no SQLite equivalent, so these methods can't be
exercised end-to-end against a local SQLite DB. Instead we test in two
layers:

1. Statement construction / parameter binding: a recording fake AsyncSession
   captures the statement passed to `execute()`; we assert it compiles
   (`str(stmt)`) and contains the expected pgvector fragments, and that the
   bound params match the call arguments. We also feed back canned rows to
   verify the row -> MemoryEntry mapping.
2. A `OPENCITADEL_RUN_POSTGRES_INTEGRATION`-gated test that exercises the
   real query against Postgres, following the existing `*_postgres.py`
   convention (e.g. test_db_resource_binding_postgres.py).
"""
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.domain.models.memory_entry import MemoryScope, MemorySource
from app.infrastructure.repositories.db_memory_entry_repository import (
    DBMemoryEntryRepository,
)

RUN_POSTGRES_INTEGRATION = os.getenv("OPENCITADEL_RUN_POSTGRES_INTEGRATION") == "1"


class _RecordingSession:
    """Fake AsyncSession that records the statement/params passed to execute()."""

    def __init__(self, rows=None):
        self.stmt = None
        self.params = None
        self._rows = rows or []

    async def execute(self, stmt, params=None):
        self.stmt = stmt
        self.params = params
        return SimpleNamespace(fetchall=lambda: self._rows)


def _row(**overrides):
    defaults = dict(
        id="mem-1",
        scope="global",
        session_id=None,
        title="title",
        content="content",
        tags=["a", "b"],
        owner_user_id="user-1",
        team_id=None,
        source="manual",
        last_used_at=None,
        use_count=3,
        created_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        distance=0.25,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_vector_search_entries_builds_pgvector_statement_with_scope_isolation():
    session = _RecordingSession()
    repo = DBMemoryEntryRepository(session)

    await repo.vector_search_entries([0.1, 0.2, 0.3], session_id="s1", limit=5)

    assert session.stmt is not None
    compiled = str(session.stmt)  # asserts the statement compiles
    assert "embedding <=> :query_vec" in compiled
    assert "memory_entries" in compiled
    # EXISTS subquery enforces team/owner scope isolation via the session row
    assert "EXISTS" in compiled
    assert "sessions" in compiled
    assert "team_id" in compiled
    assert "owner_user_id" in compiled

    assert session.params == {
        "session_id": "s1",
        "query_vec": "[0.1,0.2,0.3]",
        "limit": 5,
    }


@pytest.mark.asyncio
async def test_vector_search_entries_maps_rows_to_memory_entries():
    session = _RecordingSession(rows=[_row(distance=0.2), _row(id="mem-2", distance=None)])
    repo = DBMemoryEntryRepository(session)

    entries = await repo.vector_search_entries([0.1], limit=2)

    assert len(entries) == 2
    first = entries[0]
    assert first.id == "mem-1"
    assert first.scope == MemoryScope.GLOBAL
    assert first.source == MemorySource.MANUAL
    assert first.tags == ["a", "b"]
    assert first.vector_score == pytest.approx(0.8)
    second = entries[1]
    assert second.id == "mem-2"
    assert second.vector_score == pytest.approx(1.0)  # missing distance treated as 0.0


@pytest.mark.asyncio
async def test_update_embedding_executes_update_statement_for_entry():
    session = _RecordingSession()
    repo = DBMemoryEntryRepository(session)

    await repo.update_embedding("mem-1", [0.1, 0.2])

    assert session.stmt is not None
    compiled = str(session.stmt)
    assert "UPDATE memory_entries" in compiled
    assert "embedding" in compiled


@pytest.mark.skipif(
    not RUN_POSTGRES_INTEGRATION,
    reason="set OPENCITADEL_RUN_POSTGRES_INTEGRATION=1 for PostgreSQL proof",
)
@pytest.mark.asyncio
async def test_postgres_vector_search_entries_ranks_by_similarity(_db_schema):
    from sqlalchemy import insert
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.models.memory_entry import MemoryEntryORM
    from app.infrastructure.models.session import SessionModel
    from app.infrastructure.models.user import UserModel
    from app.infrastructure.security.db_authorization import (
        configure_session_authorization,
    )
    from core.config import get_settings

    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = f"vec-user-{uuid.uuid4()}"
    session_id = f"vec-session-{uuid.uuid4()}"
    close_id = f"mem-close-{uuid.uuid4()}"
    far_id = f"mem-far-{uuid.uuid4()}"

    try:
        async with session_factory() as setup:
            await configure_session_authorization(
                setup, AuthorizationContext.system("vector-search-postgres-setup")
            )
            await setup.execute(
                insert(UserModel).values(
                    id=user_id, email=f"{user_id}@test.local", username=user_id
                )
            )
            await setup.execute(
                insert(SessionModel).values(id=session_id, owner_user_id=user_id, status="pending")
            )
            await setup.execute(
                insert(MemoryEntryORM).values(
                    id=close_id,
                    scope="global",
                    title="close",
                    content="close",
                    owner_user_id=user_id,
                    embedding=[1.0] + [0.0] * 1535,
                )
            )
            await setup.execute(
                insert(MemoryEntryORM).values(
                    id=far_id,
                    scope="global",
                    title="far",
                    content="far",
                    owner_user_id=user_id,
                    embedding=[0.0] * 1535 + [1.0],
                )
            )
            await setup.commit()

        async with session_factory() as query_session:
            await configure_session_authorization(
                query_session, AuthorizationContext.system("vector-search-postgres-query")
            )
            repo = DBMemoryEntryRepository(query_session)
            results = await repo.vector_search_entries(
                [1.0] + [0.0] * 1535, session_id=session_id, limit=2
            )

        assert [r.id for r in results] == [close_id, far_id]
        assert results[0].vector_score > results[1].vector_score
    finally:
        async with session_factory() as cleanup:
            await configure_session_authorization(
                cleanup, AuthorizationContext.system("vector-search-postgres-cleanup")
            )
            from sqlalchemy import delete

            await cleanup.execute(delete(MemoryEntryORM).where(MemoryEntryORM.id.in_([close_id, far_id])))
            await cleanup.execute(delete(SessionModel).where(SessionModel.id == session_id))
            await cleanup.execute(delete(UserModel).where(UserModel.id == user_id))
            await cleanup.commit()
        await engine.dispose()
