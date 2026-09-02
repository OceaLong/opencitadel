"""PostgreSQL claim competition, lease recovery, and generation fencing."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.kernel.domain.types import EffectSafety
from app.kernel.infrastructure.postgres.claims import (
    PostgresEffectClaimStore,
    PostgresTimerClaimStore,
)
from app.kernel.infrastructure.postgres.models import (
    KERNEL_TABLES,
    KernelEffectORM,
    KernelRunORM,
    KernelTimerORM,
)

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
RUN_ID = UUID(int=7100)


@pytest_asyncio.fixture
async def claim_factory():
    uri = os.getenv("KERNEL_V2_TEST_DATABASE_URI")
    if not uri:
        pytest.skip("KERNEL_V2_TEST_DATABASE_URI is required for claim fencing proofs")
    engine = create_async_engine(uri)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE "
                + ", ".join(table.name for table in reversed(KERNEL_TABLES))
                + " RESTART IDENTITY CASCADE"
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE TABLE "
                    + ", ".join(table.name for table in reversed(KERNEL_TABLES))
                    + " RESTART IDENTITY CASCADE"
                )
            )
        await engine.dispose()


async def _seed_effect(
    factory,
    *,
    effect_id: UUID,
    safety: EffectSafety,
    status: str = "ready",
    generation: int = 0,
    lease_expires_at=None,
):
    async with factory() as session, session.begin():
        if await session.get(KernelRunORM, RUN_ID) is None:
            session.add(
                KernelRunORM(
                    id=RUN_ID,
                    workflow="agent",
                    owner_user_id="user-1",
                    team_id=None,
                    created_by_user_id="user-1",
                    stream_version=1,
                    stream_hash="a" * 64,
                    snapshot_version=1,
                    snapshot_hash="a" * 64,
                    snapshot={},
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            # The lean kernel models intentionally have no ORM relationships;
            # make the FK parent visible before inserting the Effect fixture.
            await session.flush()
        session.add(
            KernelEffectORM(
                id=effect_id,
                invocation_id=effect_id,
                run_id=RUN_ID,
                source_event_id=UUID(int=effect_id.int + 100),
                effect_type="model.call",
                safety=safety.value,
                request_ciphertext=json.dumps({"prompt": "hello"}),
                request_digest="b" * 64,
                public_summary={"kind": "model"},
                status=status,
                approval_id=None,
                timeout_seconds=30,
                max_attempts=3,
                attempt_count=1 if status == "started" else 0,
                next_attempt_at=NOW,
                claim_owner="dead-worker" if status == "started" else None,
                claim_generation=generation,
                lease_expires_at=lease_expires_at,
                heartbeat_at=NOW if status == "started" else None,
                started_at=NOW if status == "started" else None,
                result_reference=None,
                result_digest=None,
                error_code=None,
                error_message=None,
                owner_user_id="user-1",
                team_id=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )


@pytest.mark.asyncio
async def test_competing_workers_claim_an_effect_once_and_stale_start_is_rejected(
    claim_factory,
) -> None:
    """Removing SKIP LOCKED or generation predicates would duplicate an Effect."""

    effect_id = UUID(int=7101)
    await _seed_effect(
        claim_factory,
        effect_id=effect_id,
        safety=EffectSafety.READ_ONLY,
    )
    store = PostgresEffectClaimStore(claim_factory, decrypt_request=json.loads)

    first, second = await asyncio.gather(
        store.claim_ready(worker_id="worker-1", now=NOW, limit=1, lease_seconds=30),
        store.claim_ready(worker_id="worker-2", now=NOW, limit=1, lease_seconds=30),
    )
    claims = (*first, *second)

    assert len(claims) == 1
    assert claims[0].effect_id == effect_id
    assert claims[0].claim_generation == 1
    assert await store.mark_started(effect_id, 0, now=NOW) is False
    assert await store.mark_started(effect_id, 1, now=NOW) is True


@pytest.mark.asyncio
async def test_expired_unsafe_started_effect_is_persisted_unknown(claim_factory) -> None:
    """Unsafe writes must not re-enter the ready queue after a worker crash."""

    effect_id = UUID(int=7102)
    await _seed_effect(
        claim_factory,
        effect_id=effect_id,
        safety=EffectSafety.NON_IDEMPOTENT_WRITE,
        status="started",
        generation=4,
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    store = PostgresEffectClaimStore(claim_factory, decrypt_request=json.loads)

    expired = await store.recover_expired(now=NOW)

    async with claim_factory() as session:
        row = await session.scalar(select(KernelEffectORM).where(KernelEffectORM.id == effect_id))
    assert len(expired) == 1
    assert expired[0].resolution == "unknown"
    assert expired[0].claim.claim_generation == 4
    assert row is not None
    assert row.status == "unknown"


@pytest.mark.asyncio
async def test_expired_read_only_effect_returns_to_ready_with_same_invocation(
    claim_factory,
) -> None:
    """Safe recovery may retry but cannot mint a different invocation identity."""

    effect_id = UUID(int=7103)
    await _seed_effect(
        claim_factory,
        effect_id=effect_id,
        safety=EffectSafety.READ_ONLY,
        status="started",
        generation=2,
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    store = PostgresEffectClaimStore(claim_factory, decrypt_request=json.loads)

    assert await store.recover_expired(now=NOW) == ()
    claimed = await store.claim_ready(worker_id="worker-new", now=NOW, limit=1, lease_seconds=30)

    assert len(claimed) == 1
    assert claimed[0].invocation_id == effect_id
    assert claimed[0].claim_generation == 3


@pytest.mark.asyncio
async def test_competing_timer_workers_use_skip_locked_and_generation_fencing(
    claim_factory,
) -> None:
    timer_id = UUID(int=7104)
    async with claim_factory() as session, session.begin():
        session.add(
            KernelRunORM(
                id=RUN_ID,
                workflow="agent",
                owner_user_id="user-1",
                team_id=None,
                created_by_user_id="user-1",
                stream_version=1,
                stream_hash="a" * 64,
                snapshot_version=1,
                snapshot_hash="a" * 64,
                snapshot={},
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(
            KernelTimerORM(
                id=timer_id,
                run_id=RUN_ID,
                due_at=NOW,
                command_type="ExpireApproval",
                command_payload={"approval_id": str(UUID(int=7105))},
                status="pending",
                claim_owner=None,
                claim_generation=0,
                lease_expires_at=None,
                created_at=NOW,
                fired_at=None,
                owner_user_id="user-1",
                team_id=None,
            )
        )
    store = PostgresTimerClaimStore(claim_factory)

    first, second = await asyncio.gather(
        store.claim_due(worker_id="timer-1", now=NOW, limit=1, lease_seconds=30),
        store.claim_due(worker_id="timer-2", now=NOW, limit=1, lease_seconds=30),
    )
    claims = (*first, *second)

    assert len(claims) == 1
    assert claims[0].timer_id == timer_id
    assert claims[0].claim_generation == 1
    assert await store.mark_fired(timer_id, 0, now=NOW) is False
    assert await store.mark_fired(timer_id, 1, now=NOW) is True
