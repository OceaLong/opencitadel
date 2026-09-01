"""C3 safe-watermark proof: out-of-order scope commits never drop events.

``execution_events.position`` is a global BIGSERIAL: the value is assigned at
INSERT time but only becomes visible at COMMIT time. Before the fix the append
path held only a *per-stream* advisory lock, so two different streams in the
same owner scope could commit out of position order — position ``N+1`` visible
before ``N`` — letting the scope projector advance its checkpoint past ``N`` and
drop it forever.

The fix holds a *per owner-scope* advisory lock across the whole append
transaction, so within a scope a higher position cannot even be assigned until
the transaction holding the lower one commits. These tests exercise the real
append + projection path against PostgreSQL and prove:

* appends to two different streams in the same scope are serialized (the second
  blocks while the first holds the scope lock) — the discriminating assertion
  that fails against the old per-stream lock; and
* after the interleaving the projector processes **both** positions, skipping
  neither, and ``execution_scope_head`` / ``list_pending`` track the scope
  correctly (C3b).
"""

import asyncio
import contextlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.execution.events import NewEvent
from app.domain.execution.store import AppendContext, StreamRef
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.models import (
    ExecutionProjectorCheckpointORM,
    ExecutionPublicEventORM,
    ExecutionScopeHeadORM,
)
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
from app.infrastructure.execution.postgres_owner_scope_source import PostgresOwnerScopeSource
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_session,
    execution_kernel_database_uri,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _new_event(marker: str) -> NewEvent:
    # "RunStarted" projects a "session_status" public event keyed by position,
    # giving a per-position side effect that makes a skipped position visible
    # without requiring full Run-aggregate semantics.
    return NewEvent(
        event_type="RunStarted",
        event_schema_version=1,
        public_payload={"marker": marker},
        internal_payload={},
    )


def _context(owner_user_id: str) -> AppendContext:
    return AppendContext(
        owner_user_id=owner_user_id,
        team_id=None,
        correlation_id=uuid4(),
        causation_id=uuid4(),
        occurred_at=NOW,
    )


async def _configured(session):
    await configure_session_authorization(
        session,
        AuthorizationContext.system("scope-watermark-test"),
    )
    return session


async def _cleanup_scope(owner_user_id: str) -> None:
    async with execution_admin_session() as session:
        await session.execute(
            text("ALTER TABLE execution_events DISABLE TRIGGER execution_events_immutable")
        )
        await session.execute(
            text("DELETE FROM execution_events WHERE owner_user_id = :owner"),
            {"owner": owner_user_id},
        )
        await session.execute(
            text("ALTER TABLE execution_events ENABLE TRIGGER execution_events_immutable")
        )
        await session.execute(
            text(
                "ALTER TABLE execution_stream_owners "
                "DISABLE TRIGGER execution_stream_owners_immutable"
            )
        )
        await session.execute(
            text("DELETE FROM execution_stream_owners WHERE owner_user_id = :owner"),
            {"owner": owner_user_id},
        )
        await session.execute(
            text(
                "ALTER TABLE execution_stream_owners "
                "ENABLE TRIGGER execution_stream_owners_immutable"
            )
        )
        await session.execute(
            text("DELETE FROM execution_public_events WHERE owner_user_id = :owner"),
            {"owner": owner_user_id},
        )
        await session.execute(
            text("DELETE FROM execution_projector_checkpoints WHERE owner_user_id = :owner"),
            {"owner": owner_user_id},
        )
        await session.execute(
            text("DELETE FROM execution_scope_head WHERE owner_scope_key = :key"),
            {"key": f"user:{owner_user_id}"},
        )
        await session.commit()


@pytest.fixture
async def kernel_factory(_db_schema):
    engine = create_async_engine(execution_kernel_database_uri())
    factory = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_out_of_order_scope_commit_is_serialized_and_never_skipped(
    kernel_factory,
) -> None:
    owner_user_id = f"watermark-user-{uuid4()}"
    scope = OwnerScope.personal(owner_user_id)
    stream_a = StreamRef(stream_type="synthetic_scope", stream_id=f"a-{uuid4()}")
    stream_b = StreamRef(stream_type="synthetic_scope", stream_id=f"b-{uuid4()}")

    projector = PostgresFormalProjector(
        session_factory=kernel_factory,
        authorization=AuthorizationContext.system("scope-watermark-projector"),
    )
    scope_source = PostgresOwnerScopeSource(
        session_factory=kernel_factory,
        authorization=AuthorizationContext.system("scope-watermark-scopes"),
    )

    session_a = kernel_factory()
    session_b = kernel_factory()
    append_b: asyncio.Task | None = None
    try:
        await _configured(session_a)
        # Stream A append holds the per-scope advisory lock; NOT yet committed,
        # so position P_A is assigned but invisible to other transactions.
        result_a = await PostgresEventStore(session_a).append(
            stream_a,
            0,
            (_new_event("a"),),
            _context(owner_user_id),
        )
        pos_a = result_a.last_position

        # Stream B (same scope, different stream) must block on the scope lock.
        await _configured(session_b)
        append_b = asyncio.create_task(
            PostgresEventStore(session_b).append(
                stream_b,
                0,
                (_new_event("b"),),
                _context(owner_user_id),
            )
        )

        # Discriminator: under the fixed per-scope lock the second append cannot
        # make progress while A holds the lock. Under the old per-stream lock B
        # would append immediately and this would NOT time out.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(append_b), timeout=2.0)
        assert not append_b.done()

        # Isolate the *advisory lock* (not merely the scope_head row lock): with
        # the per-scope lock acquired BEFORE the INSERT, B cannot even consume a
        # BIGSERIAL value while blocked, so the sequence high-water mark is still
        # exactly P_A. A per-stream lock would let B run nextval() and assign
        # P_A+1 before blocking later on the scope_head upsert — the precise race
        # window that permits an out-of-order commit and a skipped position.
        async with kernel_factory() as probe:
            await _configured(probe)
            seq_high = await probe.scalar(
                text("SELECT last_value FROM execution_events_position_seq")
            )
        assert seq_high == pos_a

        # While A is in flight and B is blocked, nothing is projectable yet and
        # the scope is not advertised as pending (no visible head).
        empty = await projector.run_once(scope, limit=100)
        assert empty.processed == 0
        assert scope not in await scope_source.list_pending(limit=100)

        # Release the scope lock; B now proceeds and MUST get a higher position.
        await session_a.commit()
        result_b = await append_b
        pos_b = result_b.last_position
        await session_b.commit()
        assert pos_a < pos_b

        # The scope is now pending (head advanced beyond the checkpoint) ...
        assert scope in await scope_source.list_pending(limit=100)

        # ... and a single projection run processes BOTH positions in order.
        projected = await projector.run_once(scope, limit=100)
        assert projected.processed == 2
        assert projected.last_position == pos_b

        async with kernel_factory() as verify:
            await _configured(verify)
            positions = set(
                (
                    await verify.execute(
                        select(ExecutionPublicEventORM.position).where(
                            ExecutionPublicEventORM.owner_user_id == owner_user_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            checkpoint = await verify.scalar(
                select(ExecutionProjectorCheckpointORM.last_position).where(
                    ExecutionProjectorCheckpointORM.owner_scope_key == f"user:{owner_user_id}"
                )
            )
            head = await verify.scalar(
                select(ExecutionScopeHeadORM.head_position).where(
                    ExecutionScopeHeadORM.owner_scope_key == f"user:{owner_user_id}"
                )
            )

        # The lower position P_A was NOT skipped: both public events exist.
        assert positions == {pos_a, pos_b}
        assert checkpoint == pos_b
        assert head == pos_b

        # Caught up: the scope is no longer pending (head == checkpoint).
        assert scope not in await scope_source.list_pending(limit=100)
    finally:
        if append_b is not None:
            if not append_b.done():
                append_b.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await append_b
        await session_a.close()
        await session_b.close()
        await _cleanup_scope(owner_user_id)


@pytest.mark.asyncio
async def test_scope_head_drives_list_pending(kernel_factory) -> None:
    owner_user_id = f"watermark-head-{uuid4()}"
    scope = OwnerScope.personal(owner_user_id)
    stream = StreamRef(stream_type="synthetic_scope", stream_id=f"head-{uuid4()}")

    scope_source = PostgresOwnerScopeSource(
        session_factory=kernel_factory,
        authorization=AuthorizationContext.system("scope-head-scopes"),
    )
    projector = PostgresFormalProjector(
        session_factory=kernel_factory,
        authorization=AuthorizationContext.system("scope-head-projector"),
    )
    try:
        # A scope with no appended events is never pending.
        assert scope not in await scope_source.list_pending(limit=100)

        async with kernel_factory() as session:
            await _configured(session)
            result = await PostgresEventStore(session).append(
                stream,
                0,
                (_new_event("h1"), _new_event("h2")),
                _context(owner_user_id),
            )
            await session.commit()
        head_position = result.last_position

        async with kernel_factory() as verify:
            await _configured(verify)
            head = await verify.scalar(
                select(ExecutionScopeHeadORM.head_position).where(
                    ExecutionScopeHeadORM.owner_scope_key == f"user:{owner_user_id}"
                )
            )
        assert head == head_position

        # head > checkpoint(absent) -> pending.
        assert scope in await scope_source.list_pending(limit=100)

        await projector.run_once(scope, limit=100)

        # head == checkpoint -> no longer pending.
        assert scope not in await scope_source.list_pending(limit=100)
    finally:
        await _cleanup_scope(owner_user_id)
