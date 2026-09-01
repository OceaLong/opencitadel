"""Canonical PostgreSQL proof for session soft-delete + recycle bin (E9).

Covers: normal list/get hide soft-deleted rows, recycle bin lists them,
restore brings them back, purge physically removes them, and every mutation
is owner-scope isolated.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session
from app.infrastructure.repositories.db_session_repository import DBSessionRepository
from tests.app.execution_test_support import execution_admin_session


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgres_integration")
async def test_session_soft_delete_recycle_restore_purge_and_scope_isolation():
    suffix = uuid.uuid4().hex
    owner_a = f"user-a-{suffix}"
    owner_b = f"user-b-{suffix}"
    sid_a = f"sess-a-{suffix}"
    sid_b = f"sess-b-{suffix}"
    scope_a = OwnerScope.personal(owner_a)
    scope_b = OwnerScope.personal(owner_b)

    async with execution_admin_session() as db:
        for owner in (owner_a, owner_b):
            await db.execute(
                text("INSERT INTO users (id, email, username) VALUES (:id, :email, :username)"),
                {"id": owner, "email": f"{owner}@example.test", "username": owner},
            )
        repo = DBSessionRepository(db)
        await repo.save(Session(id=sid_a, title="A", owner_user_id=owner_a))
        await repo.save(Session(id=sid_b, title="B", owner_user_id=owner_b))
        await db.commit()

        try:
            # 1. Live sessions are visible on normal read paths.
            assert await repo.get_by_id(sid_a, scope=scope_a) is not None
            assert {s.id for s in await repo.get_all(scope=scope_a)} == {sid_a}
            assert await repo.list_deleted(scope=scope_a) == []

            # 2. Soft delete A; a foreign scope cannot soft-delete B.
            assert await repo.soft_delete(sid_a, scope=scope_a) is True
            assert await repo.soft_delete(sid_b, scope=scope_a) is False
            await db.commit()

            # 3. A is hidden from normal reads but present in the recycle bin.
            assert await repo.get_by_id(sid_a, scope=scope_a) is None
            assert sid_a not in {s.id for s in await repo.get_all(scope=scope_a)}
            assert {s.id for s in await repo.list_deleted(scope=scope_a)} == {sid_a}
            # scope isolation: B's owner sees an empty recycle bin.
            assert await repo.list_deleted(scope=scope_b) == []
            # scope isolation: a foreign scope cannot restore A.
            assert await repo.restore(sid_a, scope=scope_b) is False
            await db.commit()

            # 4. Restore A -> reappears on normal reads, leaves the recycle bin.
            assert await repo.restore(sid_a, scope=scope_a) is True
            await db.commit()
            assert await repo.get_by_id(sid_a, scope=scope_a) is not None
            assert await repo.list_deleted(scope=scope_a) == []

            # 5. Purge only targets recycle-bin rows; then removes A physically.
            assert await repo.purge(sid_a, scope=scope_a) is False  # A is live again
            assert await repo.soft_delete(sid_a, scope=scope_a) is True
            await db.commit()
            assert await repo.purge(sid_a, scope=scope_a) is True
            await db.commit()
            assert await repo.exists(sid_a) is False
            assert await repo.get_by_id(sid_b, scope=scope_b) is not None
        finally:
            await repo.delete_by_id(sid_a)
            await repo.delete_by_id(sid_b)
            await db.execute(
                text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": [owner_a, owner_b]},
            )
            await db.commit()
