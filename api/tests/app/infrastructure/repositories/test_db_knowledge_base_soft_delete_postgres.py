"""Canonical PostgreSQL proof for knowledge-base soft-delete + recycle bin (E9).

Covers: normal get/list hide soft-deleted KBs, recycle bin lists them, restore
brings them back, purge physically removes them, and every mutation is
owner-scope isolated.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.domain.models.knowledge_base import KnowledgeBase
from app.domain.models.scope import OwnerScope
from app.infrastructure.repositories.db_knowledge_base_repository import (
    DBKnowledgeBaseRepository,
)
from tests.app.execution_test_support import execution_admin_session


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgres_integration")
async def test_kb_soft_delete_recycle_restore_purge_and_scope_isolation():
    suffix = uuid.uuid4().hex
    owner_a = f"kb-user-a-{suffix}"
    owner_b = f"kb-user-b-{suffix}"
    kb_a = f"kb-a-{suffix}"
    kb_b = f"kb-b-{suffix}"
    scope_a = OwnerScope.personal(owner_a)
    scope_b = OwnerScope.personal(owner_b)

    async with execution_admin_session() as db:
        for owner in (owner_a, owner_b):
            await db.execute(
                text("INSERT INTO users (id, email, username) VALUES (:id, :email, :username)"),
                {"id": owner, "email": f"{owner}@example.test", "username": owner},
            )
        repo = DBKnowledgeBaseRepository(db)
        await repo.save_kb(KnowledgeBase(id=kb_a, name="A", owner_user_id=owner_a))
        await repo.save_kb(KnowledgeBase(id=kb_b, name="B", owner_user_id=owner_b))
        await db.commit()

        try:
            # 1. Live KBs are visible on normal read paths.
            assert await repo.get_kb(kb_a, scope=scope_a) is not None
            assert {k.id for k in await repo.list_kbs(scope=scope_a)} == {kb_a}
            assert await repo.list_deleted_kbs(scope=scope_a) == []

            # 2. Soft delete A; a foreign scope cannot soft-delete B.
            assert await repo.soft_delete(kb_a, scope=scope_a) is True
            assert await repo.soft_delete(kb_b, scope=scope_a) is False
            await db.commit()

            # 3. A is hidden from normal reads but present in the recycle bin.
            assert await repo.get_kb(kb_a, scope=scope_a) is None
            assert kb_a not in {k.id for k in await repo.list_kbs(scope=scope_a)}
            assert {k.id for k in await repo.list_deleted_kbs(scope=scope_a)} == {kb_a}
            # scope isolation.
            assert await repo.list_deleted_kbs(scope=scope_b) == []
            assert await repo.restore(kb_a, scope=scope_b) is False
            await db.commit()

            # 4. Restore A -> reappears on normal reads, leaves the recycle bin.
            assert await repo.restore(kb_a, scope=scope_a) is True
            await db.commit()
            assert await repo.get_kb(kb_a, scope=scope_a) is not None
            assert await repo.list_deleted_kbs(scope=scope_a) == []

            # 5. purge_kb only targets recycle-bin rows; then removes A physically.
            assert await repo.purge_kb(kb_a, scope=scope_a) is False  # A is live again
            assert await repo.soft_delete(kb_a, scope=scope_a) is True
            await db.commit()
            # foreign scope cannot purge A.
            assert await repo.purge_kb(kb_a, scope=scope_b) is False
            assert await repo.purge_kb(kb_a, scope=scope_a) is True
            await db.commit()

            leftover = await db.execute(
                text("SELECT 1 FROM knowledge_bases WHERE id = :id"),
                {"id": kb_a},
            )
            assert leftover.scalar_one_or_none() is None
            assert await repo.get_kb(kb_b, scope=scope_b) is not None
        finally:
            await db.execute(
                text("DELETE FROM knowledge_bases WHERE id = ANY(:ids)"),
                {"ids": [kb_a, kb_b]},
            )
            await db.execute(
                text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": [owner_a, owner_b]},
            )
            await db.commit()
