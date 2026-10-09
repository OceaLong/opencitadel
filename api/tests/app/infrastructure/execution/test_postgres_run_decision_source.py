"""Poison-row isolation for the formal Run decision source (real PostgreSQL)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.execution.run import RunFamily, RunState, RunStatus
from app.domain.execution.serialization import canonical_state_hash
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import (
    ExecutionPoisonedRunORM,
    ExecutionRunProjectionORM,
)
from app.infrastructure.execution.postgres_run_decision_source import (
    PostgresRunDecisionSource,
)
from core.config import load_deployment_settings
from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.execution_database_fixture import (
    isolated_execution_database as _db_schema,  # noqa: F401
)
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_session,
    execution_kernel_database_uri,
    run_policy_snapshot_json,
)

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


def _state(
    run_id,
    correlation_id,
    *,
    status: RunStatus = RunStatus.RUNNING,
    wait_reason: str | None = None,
) -> RunState:
    return RunState(
        run_id=run_id,
        family=RunFamily.AGENT,
        source_entity_type="session",
        source_entity_id=str(run_id),
        semantic_payload={},
        policy_snapshot=run_policy_snapshot_json(RunFamily.AGENT),
        status=status,
        wait_reason=wait_reason,
        stream_version=2,
        owner_user_id="decision-user",
        correlation_id=correlation_id,
    )


def _projection(
    state: RunState,
    *,
    updated_at: datetime,
    state_hash: str,
    decision_due_at: datetime | None = None,
    armed: bool = True,
):
    snapshot = run_policy_snapshot_json(RunFamily.AGENT)
    # Mirrors the formal projector's arming rule: queued / idle-running rows
    # carry decision_due_at, waiting rows do not (unless the caller overrides).
    if decision_due_at is None and armed and state.status in (RunStatus.QUEUED, RunStatus.RUNNING):
        decision_due_at = updated_at
    return ExecutionRunProjectionORM(
        run_id=state.run_id,
        family=state.family.value,
        source_entity_type=state.source_entity_type,
        source_entity_id=state.source_entity_id,
        execution_policy_revision_id=snapshot["execution_revision_id"],
        execution_policy_digest=snapshot["execution_policy_digest"],
        status=state.status.value,
        terminal=False,
        wait_reason=state.wait_reason,
        active_activity_count=len(state.active_activity_ids),
        decision_due_at=decision_due_at,
        parent_run_id=None,
        correlation_id=state.correlation_id,
        owner_user_id=state.owner_user_id,
        team_id=None,
        stream_version=state.stream_version,
        last_event_position=2,
        state=state.model_dump(mode="json"),
        state_hash=state_hash,
        last_event_hash="a" * 64,
        created_at=updated_at,
        updated_at=updated_at,
        terminal_at=None,
    )


def _metric() -> float:
    return REGISTRY.get_sample_value("execution_poisoned_runs_total", {}) or 0.0


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_poison_row_is_quarantined_without_aborting_the_batch() -> None:
    poison_id = uuid4()
    healthy_id = uuid4()
    poison_state = _state(poison_id, uuid4())
    healthy_state = _state(healthy_id, uuid4())

    async with execution_admin_session() as session:
        # The poison row's stored hash does not match its state -> corrupt.
        session.add(_projection(poison_state, updated_at=NOW, state_hash="0" * 64))
        session.add(
            _projection(
                healthy_state,
                updated_at=NOW.replace(minute=1),
                state_hash=canonical_state_hash(healthy_state),
            )
        )
        await session.commit()

    engine = create_async_engine(execution_kernel_database_uri())
    sessions = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    source = PostgresRunDecisionSource(
        session_factory=sessions,
        authorization=AuthorizationContext.system("decision-poison-test"),
    )
    try:
        before = _metric()
        candidates = await source.load_ready(limit=10)

        # The corrupt row is skipped; the healthy Run is still decided.
        run_ids = {candidate.state.run_id for candidate in candidates}
        assert healthy_id in run_ids
        assert poison_id not in run_ids
        assert _metric() - before == 1.0

        async with execution_admin_session() as session:
            quarantined = await session.get(ExecutionPoisonedRunORM, poison_id)
            assert quarantined is not None
            assert quarantined.owner_user_id == "decision-user"
            assert "hash" in quarantined.last_error

        # A subsequent scan skips the already-quarantined row (no re-poisoning)
        # and still returns the healthy Run.
        after = _metric()
        again = await source.load_ready(limit=10)
        assert {candidate.state.run_id for candidate in again} == {healthy_id}
        assert _metric() == after
    finally:
        await engine.dispose()
        async with execution_admin_session() as session:
            await session.execute(
                delete(ExecutionPoisonedRunORM).where(
                    ExecutionPoisonedRunORM.run_id.in_([poison_id, healthy_id])
                )
            )
            await session.execute(
                delete(ExecutionRunProjectionORM).where(
                    ExecutionRunProjectionORM.run_id.in_([poison_id, healthy_id])
                )
            )
            await session.commit()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_hundred_waiting_approvals_do_not_starve_a_new_queued_run() -> None:
    """P0-1 根治场景 (K2-1): with 100 WAITING(approval) Runs in the projection,
    a freshly queued Run is returned by a single load_ready round — waiting
    rows are not even scanned because their decision_due_at is NULL."""
    from sqlalchemy import update as sa_update

    waiting_ids = [uuid4() for _ in range(100)]
    queued_id = uuid4()
    async with execution_admin_session() as session:
        for index, run_id in enumerate(waiting_ids):
            state = _state(
                run_id,
                uuid4(),
                status=RunStatus.WAITING,
                wait_reason="approval",
            )
            session.add(
                _projection(
                    state,
                    # All waiting rows are older than the queued run, so a
                    # naive updated_at scan with limit=10 would starve it.
                    updated_at=NOW.replace(second=index % 60, minute=index // 60),
                    state_hash=canonical_state_hash(state),
                )
            )
        queued_state = _state(queued_id, uuid4(), status=RunStatus.QUEUED)
        session.add(
            _projection(
                queued_state,
                updated_at=NOW.replace(hour=13),
                state_hash=canonical_state_hash(queued_state),
            )
        )
        await session.commit()

    engine = create_async_engine(execution_kernel_database_uri())
    sessions = authenticated_session_factory(
        engine,
        signing_secret=load_deployment_settings().session_secret,
    )
    source = PostgresRunDecisionSource(
        session_factory=sessions,
        authorization=AuthorizationContext.system("decision-readiness-test"),
    )
    try:
        candidates = await source.load_ready(limit=10)
        run_ids = {candidate.state.run_id for candidate in candidates}

        assert queued_id in run_ids
        assert run_ids.isdisjoint(set(waiting_ids))

        # Disarm removes the row from subsequent ready scans...
        await source.disarm([queued_id])
        assert await source.load_ready(limit=10) == ()

        # ...but a projector advance after the load re-arms and wins over a
        # stale disarm (the optimistic guard on last_event_position).
        async with execution_admin_session() as session:
            await session.execute(
                sa_update(ExecutionRunProjectionORM)
                .where(ExecutionRunProjectionORM.run_id == queued_id)
                .values(decision_due_at=NOW, last_event_position=99)
            )
            await session.commit()
        rearmed = await source.load_ready(limit=10)
        assert {candidate.state.run_id for candidate in rearmed} == {queued_id}
        # Stale disarm attempt using positions captured before the advance: the
        # source reloaded (positions updated), so simulate staleness directly.
        source._armed_positions[queued_id] = 2
        await source.disarm([queued_id])
        still_armed = await source.load_ready(limit=10)
        assert {candidate.state.run_id for candidate in still_armed} == {queued_id}
    finally:
        await engine.dispose()
        async with execution_admin_session() as session:
            await session.execute(
                delete(ExecutionRunProjectionORM).where(
                    ExecutionRunProjectionORM.run_id.in_([*waiting_ids, queued_id])
                )
            )
            await session.execute(
                delete(ExecutionPoisonedRunORM).where(
                    ExecutionPoisonedRunORM.run_id.in_([*waiting_ids, queued_id])
                )
            )
            await session.commit()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_planner_retries_are_durable_bounded_and_verified_recovery_rearms():
    from datetime import timedelta

    from app.domain.models.scope import OwnerScope

    run_id = uuid4()
    state = _state(run_id, uuid4()).model_copy(update={"owner_user_id": f"recovery-{run_id}"})
    now = datetime.now(UTC)
    async with execution_admin_session() as session:
        session.add(_projection(state, updated_at=now, state_hash=canonical_state_hash(state)))
        await session.commit()
    engine = create_async_engine(execution_kernel_database_uri())
    sessions = authenticated_session_factory(
        engine, signing_secret=load_deployment_settings().session_secret
    )
    source = PostgresRunDecisionSource(
        session_factory=sessions, authorization=AuthorizationContext.system("recovery-test")
    )
    try:
        for attempt in range(1, 4):
            candidates = await source.load_ready(limit=1000)
            assert run_id in [candidate.state.run_id for candidate in candidates]
            await source.failed(run_id, error="ValueError", now=datetime.now(UTC))
            async with execution_admin_session() as session:
                failure = await session.get(ExecutionPoisonedRunORM, run_id)
                assert failure.failure_count == attempt
                assert (failure.next_attempt_at is None) == (attempt == 3)
                assert run_id not in [c.state.run_id for c in await source.load_ready(limit=1000)]
                if attempt < 3:
                    projection = await session.get(ExecutionRunProjectionORM, run_id)
                    failure.next_attempt_at = projection.decision_due_at = now - timedelta(
                        seconds=1
                    )
                    await session.commit()
        # Rebuild supplies an intact projection and restores readiness; recovery
        # verifies its hash and needed payloads before clearing the marker.
        async with execution_admin_session() as session:
            projection = await session.get(ExecutionRunProjectionORM, run_id)
            projection.decision_due_at = now
            await session.commit()
        assert await source.recover_scope(OwnerScope.personal(state.owner_user_id)) == (run_id,)
        assert run_id in [c.state.run_id for c in await source.load_ready(limit=1000)]
    finally:
        async with execution_admin_session() as session:
            await session.execute(
                delete(ExecutionPoisonedRunORM).where(ExecutionPoisonedRunORM.run_id == run_id)
            )
            await session.execute(
                delete(ExecutionRunProjectionORM).where(ExecutionRunProjectionORM.run_id == run_id)
            )
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_scope_recovery_keeps_missing_decision_payload_quarantined_and_releases_valid_run():
    from app.domain.execution.run import decision_data_digest
    from app.domain.models.scope import OwnerScope

    missing_id, valid_id, missing_activity_id = uuid4(), uuid4(), uuid4()
    owner_id = f"payload-recovery-{uuid4()}"
    missing_state = _state(missing_id, uuid4()).model_copy(
        update={
            "owner_user_id": owner_id,
            "activity_results": (
                (
                    missing_activity_id,
                    0,
                    "object://missing-decision-result",
                    "A model decision whose required payload was lost",
                    decision_data_digest({"tool_calls": [{"name": "required-tool"}]}),
                ),
            ),
        }
    )
    valid_state = _state(valid_id, uuid4()).model_copy(update={"owner_user_id": owner_id})
    run_ids = [missing_id, valid_id]
    engine = create_async_engine(execution_kernel_database_uri())
    sessions = authenticated_session_factory(
        engine, signing_secret=load_deployment_settings().session_secret
    )
    source = PostgresRunDecisionSource(
        session_factory=sessions,
        authorization=AuthorizationContext.system("required-payload-recovery-test"),
    )
    try:
        async with execution_admin_session() as session:
            for state in (missing_state, valid_state):
                # Both projections have valid hashes: rebuilding the missing
                # payload Run's projection alone must not authorize its release.
                session.add(
                    _projection(state, updated_at=NOW, state_hash=canonical_state_hash(state))
                )
                session.add(
                    ExecutionPoisonedRunORM(
                        run_id=state.run_id,
                        owner_user_id=owner_id,
                        team_id=None,
                        reason="planner_error",
                        last_error="Previously isolated",
                        failure_count=3,
                        next_attempt_at=None,
                        first_seen_at=NOW,
                        last_seen_at=NOW,
                    )
                )
            await session.commit()

        recovered = await source.recover_scope(OwnerScope.personal(owner_id))
        assert recovered == (valid_id,)
        async with execution_admin_session() as session:
            missing = await session.get(ExecutionPoisonedRunORM, missing_id)
            assert missing is not None
            assert missing.next_attempt_at is None
            assert str(missing_activity_id) in missing.last_error
            assert "decision payload" in missing.last_error
            assert "missing" in missing.last_error
            assert await session.get(ExecutionPoisonedRunORM, valid_id) is None

        ready_ids = {candidate.state.run_id for candidate in await source.load_ready(limit=1000)}
        assert valid_id in ready_ids
        assert missing_id not in ready_ids
        assert await source.recover_scope(OwnerScope.personal(owner_id)) == ()
    finally:
        await engine.dispose()
        async with execution_admin_session() as session:
            await session.execute(
                delete(ExecutionPoisonedRunORM).where(ExecutionPoisonedRunORM.run_id.in_(run_ids))
            )
            await session.execute(
                delete(ExecutionRunProjectionORM).where(
                    ExecutionRunProjectionORM.run_id.in_(run_ids)
                )
            )
            await session.commit()
