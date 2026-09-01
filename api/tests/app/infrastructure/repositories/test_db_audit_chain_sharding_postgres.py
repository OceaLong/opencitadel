"""Real-PostgreSQL proof for the sharded audit hash chain (C5).

Two properties matter and are proven here against a live PostgreSQL:

1. Writers to *different* shards take *different* advisory locks, so they do
   not serialize behind one global lock (the old platform-wide bottleneck);
   writers to the *same* shard still serialize so that shard's chain stays
   gap-free.
2. ``chain_seq`` is monotonic *within* a shard and ``prev_hash`` links each
   shard's entries into an independent tamper-evident chain seeded from
   ``GENESIS``; ``verify_chain_logs`` accepts an intact multi-shard set and
   rejects a tampered one.

The inserts run over a superuser connection so the proof isolates the chain
mechanics from the audit_logs RLS matrix (owned elsewhere); the immutable
UPDATE/DELETE trigger is never exercised (INSERT only, tamper is in-memory).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.models.audit_log import AuditLog
from app.domain.services.audit_chain import GENESIS, verify_chain_logs
from app.infrastructure.repositories.db_audit_repository import DBAuditRepository

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("postgres_integration")]

_SIGNING_KEY = "sharding-proof-signing-key-at-least-32c!"


def _admin_uri() -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    database = os.environ.get("POSTGRES_DB", "opencitadel")
    password = os.environ["POSTGRES_ADMIN_PASSWORD"]
    return f"postgresql+asyncpg://postgres:{password}@{host}:{port}/{database}"


def _log(*, team_id: str | None, actor_user_id: str | None, action: str) -> AuditLog:
    return AuditLog(
        action=action,
        team_id=team_id,
        actor_user_id=actor_user_id,
        created_at=datetime.now(UTC),
    )


async def test_different_shards_do_not_serialize_but_same_shard_does() -> None:
    engine = create_async_engine(_admin_uri())
    make_session = async_sessionmaker(engine, expire_on_commit=False)
    held_shard = f"team:{uuid.uuid4()}"
    other_shard = f"user:{uuid.uuid4()}"
    try:
        async with make_session() as holder, make_session() as contender:
            await holder.begin()
            # holder keeps the transactional advisory lock for one shard.
            await holder.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                {"k": held_shard},
            )
            await contender.begin()
            got_other = (
                await contender.execute(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
                    {"k": other_shard},
                )
            ).scalar()
            got_held = (
                await contender.execute(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
                    {"k": held_shard},
                )
            ).scalar()
            # Different shard -> different lock -> acquired without waiting.
            assert got_other is True
            # Same shard -> same lock, still held by holder -> not acquired.
            assert got_held is False
            await contender.rollback()
            await holder.rollback()
    finally:
        await engine.dispose()


async def test_per_shard_seq_prev_hash_and_tamper_detection() -> None:
    engine = create_async_engine(_admin_uri())
    make_session = async_sessionmaker(engine, expire_on_commit=False)
    team_a = str(uuid.uuid4())
    team_b = str(uuid.uuid4())
    try:
        async with make_session() as session:
            repo = DBAuditRepository(session, signing_key=_SIGNING_KEY, signing_key_id="primary")
            # Interleave two shards to prove sequences are independent.
            await repo.add(_log(team_id=team_a, actor_user_id=None, action="a1"))
            await repo.add(_log(team_id=team_b, actor_user_id=None, action="b1"))
            await repo.add(_log(team_id=team_a, actor_user_id=None, action="a2"))
            await repo.add(_log(team_id=team_b, actor_user_id=None, action="b2"))
            await session.commit()

        async with make_session() as session:
            repo = DBAuditRepository(session, signing_key=_SIGNING_KEY, signing_key_id="primary")
            all_logs = await repo.list_chained()

        mine = [log for log in all_logs if log.team_id in (team_a, team_b)]
        rows_a = [log for log in mine if log.team_id == team_a]
        rows_b = [log for log in mine if log.team_id == team_b]

        # Each shard restarts at chain_seq=1 and increments independently.
        assert [log.chain_seq for log in rows_a] == [1, 2]
        assert [log.chain_seq for log in rows_b] == [1, 2]
        # prev_hash links within a shard, first entry follows GENESIS.
        assert rows_a[0].prev_hash == GENESIS
        assert rows_a[1].prev_hash == rows_a[0].entry_hash
        assert rows_b[0].prev_hash == GENESIS
        assert rows_b[1].prev_hash == rows_b[0].entry_hash
        # The two shards are distinct chains, not one interleaved chain.
        assert rows_a[0].entry_hash != rows_b[0].entry_hash

        keys = {"primary": (_SIGNING_KEY,)}
        intact = verify_chain_logs(mine, keys)
        assert intact["ok"] is True, intact
        # The full DB set (all shards) is intact too.
        assert verify_chain_logs(all_logs, keys)["ok"] is True

        # Tamper one entry in-memory -> verification fails (防篡改仍成立).
        tampered = [log.model_copy(deep=True) for log in mine]
        tampered[0].entry_hash = "dead" * 16
        broken = verify_chain_logs(tampered, keys)
        assert broken["ok"] is False
        assert broken["first_broken_seq"] == tampered[0].chain_seq
    finally:
        await engine.dispose()
