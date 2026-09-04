"""Poison-scope isolation, quarantine, and operator rebuild (D12/K4-1).

Against real PostgreSQL:

* a corrupted projection row in one owner scope never stops other scopes from
  projecting; after the failure streak the scope lands in
  ``execution_poisoned_scopes`` (with metrics) and is excluded from discovery;
* the rebuild CLI flow tears the scope down, replays it to a hash-consistent
  projection, and clears the rebuild/quarantine marker;
* the scope lock never queues: a pass that cannot take the advisory lock
  reports ``busy`` instead of blocking (P2-18).
"""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.execution.events import NewEvent
from app.domain.execution.run import RunState
from app.domain.execution.serialization import canonical_state_hash
from app.domain.execution.store import AppendContext, StreamRef
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.execution_kernel import ExecutionKernelRuntime
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
from app.infrastructure.execution.postgres_owner_scope_source import PostgresOwnerScopeSource
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_session,
    execution_kernel_database_uri,
    run_policy_snapshot_json,
)

NOW = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)


def _run_created(session_id: str) -> NewEvent:
    return NewEvent(
        event_type="RunCreated",
        event_schema_version=1,
        public_payload={
            "family": "agent",
            "source_entity_type": "session",
            "source_entity_id": session_id,
            "parent_run_id": None,
            "input": {},
        },
        internal_payload={
            "semantic_payload": {},
            "policy_snapshot": run_policy_snapshot_json("agent"),
        },
    )


def _run_started() -> NewEvent:
    return NewEvent(
        event_type="RunStarted",
        event_schema_version=1,
        public_payload={},
        internal_payload={},
    )


async def _append(kernel_factory, owner: str, run_id, events, expected_version: int) -> None:
    stream = StreamRef(stream_type="run", stream_id=str(run_id))
    async with kernel_factory() as session:
        await configure_session_authorization(
            session, AuthorizationContext.system("quarantine-append")
        )
        await PostgresEventStore(session).append(
            stream,
            expected_version,
            events,
            AppendContext(
                owner_user_id=owner,
                team_id=None,
                correlation_id=uuid4(),
                causation_id=uuid4(),
                occurred_at=NOW,
            ),
        )
        await session.commit()


async def _corrupt_projection_row(run_id) -> None:
    async with execution_admin_session() as session:
        await session.execute(
            text("UPDATE execution_run_projection SET state_hash = :bad WHERE run_id = :run_id"),
            {"bad": "f" * 64, "run_id": run_id},
        )
        await session.commit()


async def _cleanup(owners: list[str]) -> None:
    async with execution_admin_session() as session:
        for owner in owners:
            await session.execute(
                text("DELETE FROM execution_outbox WHERE owner_user_id = :owner"),
                {"owner": owner},
            )
            for table, trigger in (
                ("execution_events", "execution_events_immutable"),
                ("execution_stream_owners", "execution_stream_owners_immutable"),
            ):
                await session.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
                await session.execute(
                    text(f"DELETE FROM {table} WHERE owner_user_id = :owner"),
                    {"owner": owner},
                )
                await session.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))
            for table in (
                "execution_public_events",
                "execution_run_projection",
                "execution_projector_checkpoints",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE owner_user_id = :owner"),
                    {"owner": owner},
                )
            for table in ("execution_scope_head", "execution_poisoned_scopes"):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE owner_scope_key = :key"),
                    {"key": f"user:{owner}"},
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


def _runtime(projector, scopes) -> ExecutionKernelRuntime:
    return ExecutionKernelRuntime(
        command_handler=None,
        inbox_worker=None,
        activity_worker=None,
        decision_worker=None,
        outbox_dispatcher=None,
        timer_dispatcher=None,
        projector=projector,
        owner_scopes=scopes,
        metrics=None,
        activity_registry=None,
    )


def _sample(name: str, labels: dict | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


@pytest.mark.asyncio
async def test_poison_scope_is_quarantined_while_other_scopes_keep_projecting(
    kernel_factory,
) -> None:
    poison_owner = f"quarantine-poison-{uuid4()}"
    healthy_owner = f"quarantine-healthy-{uuid4()}"
    poison_run = uuid4()
    healthy_run = uuid4()
    projector = PostgresFormalProjector(
        session_factory=kernel_factory,
        authorization=AuthorizationContext.system("quarantine-projector"),
    )
    scopes = PostgresOwnerScopeSource(
        session_factory=kernel_factory,
        authorization=AuthorizationContext.system("quarantine-scopes"),
    )
    runtime = _runtime(projector, scopes)
    try:
        # Seed both scopes and project them clean once.
        await _append(kernel_factory, poison_owner, poison_run, (_run_created("s-p"),), 0)
        await _append(kernel_factory, healthy_owner, healthy_run, (_run_created("s-h"),), 0)
        first = await runtime.run_pending_projectors_once()
        assert first.processed == 2

        # Corrupt the poison scope's projection row, then make both scopes
        # pending again.
        await _corrupt_projection_row(poison_run)
        await _append(kernel_factory, poison_owner, poison_run, (_run_started(),), 1)
        await _append(kernel_factory, healthy_owner, healthy_run, (_run_started(),), 1)

        mismatch_before = _sample(
            "execution_replay_failures_total", {"reason": "projection_hash_mismatch"}
        )
        poisoned_before = _sample("execution_poisoned_scopes_total")

        # Pass 1: poison scope fails, healthy scope still advances.
        second = await runtime.run_pending_projectors_once()
        assert second.processed == 1
        # Passes 2 and 3: only the poison scope is pending; the third failure
        # crosses the streak threshold and quarantines it durably.
        await runtime.run_pending_projectors_once()
        await runtime.run_pending_projectors_once()

        async with execution_admin_session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT reason, failure_count, rebuilding "
                        "FROM execution_poisoned_scopes WHERE owner_scope_key = :key"
                    ),
                    {"key": f"user:{poison_owner}"},
                )
            ).one()
        assert row.reason == "ValueError"
        assert row.failure_count == 3
        assert row.rebuilding is False
        assert (
            _sample("execution_replay_failures_total", {"reason": "projection_hash_mismatch"})
            >= mismatch_before + 3
        )
        assert _sample("execution_poisoned_scopes_total") == poisoned_before + 1

        # Quarantined scopes are excluded from discovery entirely.
        pending = await scopes.list_pending(limit=100)
        assert f"user:{poison_owner}" not in {
            f"user:{scope.user_id}" if scope.team_id is None else f"team:{scope.team_id}"
            for scope in pending
        }
        final = await runtime.run_pending_projectors_once()
        assert final.processed == 0
    finally:
        await _cleanup([poison_owner, healthy_owner])


@pytest.mark.asyncio
async def test_rebuild_cli_restores_a_quarantined_scope(kernel_factory, monkeypatch) -> None:
    """CLI smoke: rebuild replays a corrupted scope and clears its markers."""
    from app.rebuild_execution_projection import rebuild

    owner = f"rebuild-cli-{uuid4()}"
    run_id = uuid4()
    try:
        await _append(kernel_factory, owner, run_id, (_run_created("s-rebuild"), _run_started()), 0)
        projector = PostgresFormalProjector(
            session_factory=kernel_factory,
            authorization=AuthorizationContext.system("rebuild-seed-projector"),
        )
        await projector.run_once(OwnerScope.personal(owner), limit=100)
        await _corrupt_projection_row(run_id)
        # Simulate the kernel having quarantined the scope already.
        async with execution_admin_session() as session:
            await session.execute(
                text(
                    "INSERT INTO execution_poisoned_scopes "
                    "(owner_scope_key, owner_user_id, reason, last_error, failure_count) "
                    "VALUES (:key, :owner, 'ValueError', 'hash mismatch', 3)"
                ),
                {"key": f"user:{owner}", "owner": owner},
            )
            await session.commit()

        # The CLI runs with the execution-kernel database credentials.
        monkeypatch.setenv("POSTGRES_USER", os.environ["POSTGRES_KERNEL_USER"])
        monkeypatch.setenv("POSTGRES_PASSWORD", os.environ["POSTGRES_KERNEL_PASSWORD"])
        assert await rebuild(f"user:{owner}") == 0

        async with execution_admin_session() as session:
            projection = (
                await session.execute(
                    text(
                        "SELECT state, state_hash, stream_version "
                        "FROM execution_run_projection WHERE run_id = :run_id"
                    ),
                    {"run_id": run_id},
                )
            ).one()
            marker = (
                await session.execute(
                    text("SELECT 1 FROM execution_poisoned_scopes WHERE owner_scope_key = :key"),
                    {"key": f"user:{owner}"},
                )
            ).first()
            checkpoint = (
                await session.execute(
                    text(
                        "SELECT c.last_position, h.head_position "
                        "FROM execution_projector_checkpoints c "
                        "JOIN execution_scope_head h ON h.owner_scope_key = c.owner_scope_key "
                        "WHERE c.owner_scope_key = :key AND c.projector_name = 'formal'"
                    ),
                    {"key": f"user:{owner}"},
                )
            ).one()

        # The rebuilt row is hash-consistent again and fully caught up.
        rebuilt_state = RunState.model_validate(projection.state)
        assert canonical_state_hash(rebuilt_state) == projection.state_hash
        assert projection.stream_version == 2
        assert checkpoint.last_position == checkpoint.head_position
        # Rebuild marker and quarantine are both lifted.
        assert marker is None
    finally:
        await _cleanup([owner])


@pytest.mark.asyncio
async def test_scope_lock_contention_yields_busy_instead_of_queueing(kernel_factory) -> None:
    owner = f"busy-scope-{uuid4()}"
    run_id = uuid4()
    projector = PostgresFormalProjector(
        session_factory=kernel_factory,
        authorization=AuthorizationContext.system("busy-projector"),
    )
    try:
        await _append(kernel_factory, owner, run_id, (_run_created("s-busy"),), 0)

        # Another session holds the scope's advisory lock in an open
        # transaction: run_once must yield (busy) rather than block on it.
        async with kernel_factory() as blocker:
            await configure_session_authorization(
                blocker, AuthorizationContext.system("busy-blocker")
            )
            await PostgresFormalProjector._lock(blocker, f"user:{owner}")

            result = await projector.run_once(OwnerScope.personal(owner), limit=100)
            assert result.busy is True
            assert result.processed == 0
            await blocker.rollback()

        # Lock released: the same pass now projects normally.
        released = await projector.run_once(OwnerScope.personal(owner), limit=100)
        assert released.busy is False
        assert released.processed == 1
    finally:
        await _cleanup([owner])
