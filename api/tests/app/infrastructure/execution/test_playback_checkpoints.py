from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select

from app.application.execution.view_facts import ProjectionFact
from app.domain.models.playback import PlaybackBoundary
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.postgres_playback import PlaybackUnavailable, load_playback
from app.infrastructure.execution.postgres_view_observations import observe
from app.infrastructure.models.execution_view import (
    ExecutionPlaybackCheckpointORM,
    ExecutionRunViewORM,
    ExecutionViewObservationORM,
)
from tests.app.execution_test_support import execution_admin_session

pytestmark = pytest.mark.usefixtures("postgres_integration")


async def write(
    session,
    run_id,
    sequence,
    *,
    status="running",
    source_kind="formal",
    entity_id="progress-message",
):
    fact = ProjectionFact(
        sequence if source_kind == "formal" else 0,
        0,
        None,
        "run" if source_kind == "formal" else "message",
        str(run_id) if source_kind == "formal" else entity_id,
        {"status": status} if source_kind == "formal" else {"public_summary": f"phase-{sequence}"},
        source_kind,
    )
    return await observe(
        session,
        run_id=run_id,
        owner_user_id="f03-test",
        team_id=None,
        family="agent",
        fact=fact,
        source_identity=f"{source_kind}:{sequence}",
        event_id=None,
        occurred_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_terminal_observation_persists_versioned_checkpoint_in_writer_transaction():
    run_id = uuid4()
    async with execution_admin_session() as session:
        await write(session, run_id, 1)
        terminal = await write(session, run_id, 2, status="completed")
        duplicate = await write(session, run_id, 2, status="completed")
        checkpoints = (
            await session.scalars(
                select(ExecutionPlaybackCheckpointORM).where(
                    ExecutionPlaybackCheckpointORM.run_id == run_id
                )
            )
        ).all()
        assert len(checkpoints) == 1
        checkpoint = checkpoints[0]
        assert duplicate.observed_order == terminal.observed_order
        assert checkpoint.observed_order == terminal.observed_order == 2
        assert checkpoint.projector_version == 1
        assert checkpoint.state_ref["schema_version"] == 1
        assert checkpoint.state_ref["projector_version"] == 1
        assert checkpoint.state_ref["state"]["run"][str(run_id)]["status"] == "completed"
        await session.rollback()
    async with execution_admin_session() as session:
        assert (
            await session.scalar(
                select(ExecutionPlaybackCheckpointORM).where(
                    ExecutionPlaybackCheckpointORM.run_id == run_id
                )
            )
            is None
        )


@pytest.mark.asyncio
async def test_each_500_per_run_formal_events_checkpoints_without_global_position_modulo():
    run_id = uuid4()
    async with execution_admin_session() as session:
        for count in range(1, 251):
            await write(session, run_id, 10_250 + count)
        assert (
            await session.scalar(
                select(ExecutionPlaybackCheckpointORM).where(
                    ExecutionPlaybackCheckpointORM.run_id == run_id
                )
            )
            is None
        )
        for count in range(251, 501):
            await write(session, run_id, 10_250 + count)
        checkpoints = (
            await session.scalars(
                select(ExecutionPlaybackCheckpointORM).where(
                    ExecutionPlaybackCheckpointORM.run_id == run_id
                )
            )
        ).all()
        assert [(row.boundary, row.formal_position) for row in checkpoints] == [(500, 10_750)]
        await session.rollback()


@pytest.mark.asyncio
async def test_checkpoint_plus_incremental_restore_equals_full_and_reports_missing_history():
    run_id = uuid4()
    async with execution_admin_session() as session:
        await write(session, run_id, 1)
        await write(session, run_id, 2, status="completed")
        row = await write(session, run_id, 1, source_kind="progress")
        boundary = PlaybackBoundary(
            run_id=run_id,
            formal_position=row.formal_position,
            progress_position=row.progress_position,
            observed_order=row.observed_order,
            projection_revision=row.projection_revision,
            observed_at=row.observed_at,
            projector_version=1,
        )
        restored = await load_playback(
            session, boundary, trusted_scope=OwnerScope.personal("f03-test"), use_checkpoint=True
        )
        full = await load_playback(
            session, boundary, trusted_scope=OwnerScope.personal("f03-test"), use_checkpoint=False
        )
        assert restored == full
        assert restored.state["run"][str(run_id)]["status"] == "completed"
        assert restored.state["message"]["progress-message"]["public_summary"] == "phase-1"
        assert {item["reason"] for item in restored.missing_intervals} == {
            "pre_journal_progress_unavailable"
        }
        await session.rollback()


@pytest.mark.asyncio
async def test_healthy_checkpoint_restore_bounds_state_and_fact_payload_reads():
    run_id = uuid4()
    async with execution_admin_session() as session:
        await write(session, run_id, 1, status="completed")
        row = await write(session, run_id, 2, status="completed")
        boundary = PlaybackBoundary(
            run_id=run_id,
            formal_position=row.formal_position,
            progress_position=row.progress_position,
            observed_order=row.observed_order,
            projection_revision=row.projection_revision,
            observed_at=row.observed_at,
            projector_version=1,
        )
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(" ".join(statement.lower().split()))

        engine = session.bind.sync_engine
        event.listen(engine, "before_cursor_execute", capture)
        try:
            await load_playback(
                session,
                boundary,
                trusted_scope=OwnerScope.personal("f03-test"),
                use_checkpoint=True,
            )
        finally:
            event.remove(engine, "before_cursor_execute", capture)

        checkpoint_state_reads = [
            sql
            for sql in statements
            if "from execution_view_checkpoints" in sql
            and "execution_view_checkpoints.id" in sql
            and "execution_view_checkpoints.state_ref" in sql
        ]
        observation_payload_reads = [
            sql
            for sql in statements
            if "from execution_view_observations" in sql
            and "execution_view_observations.public_payload" in sql
        ]
        assert len(checkpoint_state_reads) == 1
        assert " limit " in checkpoint_state_reads[0]
        assert observation_payload_reads
        assert all("observed_order >" in sql for sql in observation_payload_reads)
        await session.rollback()


@pytest.mark.asyncio
async def test_restore_fails_closed_when_trusted_scope_does_not_match():
    run_id = uuid4()
    async with execution_admin_session() as session:
        row = await write(session, run_id, 1)
        boundary = PlaybackBoundary(
            run_id=run_id,
            formal_position=row.formal_position,
            progress_position=row.progress_position,
            observed_order=row.observed_order,
            projection_revision=row.projection_revision,
            observed_at=row.observed_at,
            projector_version=1,
        )
        with pytest.raises(PlaybackUnavailable, match="scope"):
            await load_playback(
                session, boundary, trusted_scope=OwnerScope.team("reader", "not-owner")
            )
        await session.rollback()


@pytest.mark.asyncio
async def test_checkpoint_and_full_restore_merge_target_relevant_new_coverage_gaps():
    run_id = uuid4()
    async with execution_admin_session() as session:
        await write(session, run_id, 1)
        row = await write(session, run_id, 2, status="completed")
        boundary = PlaybackBoundary(
            run_id=run_id,
            formal_position=row.formal_position,
            progress_position=row.progress_position,
            observed_order=row.observed_order,
            projection_revision=row.projection_revision,
            observed_at=row.observed_at,
            projector_version=1,
        )
        run = await session.get(ExecutionRunViewORM, run_id)
        run.completeness = {
            "state": "partial",
            "missing_fields": [],
            "missing_intervals": [
                {
                    "start": None,
                    "end": boundary.observed_at.isoformat(),
                    "reason": "retained_text_unavailable",
                },
                {
                    "start": "2999-01-01T00:00:00+00:00",
                    "end": None,
                    "reason": "future_retention_gap",
                },
            ],
        }
        await session.flush()
        checkpointed = await load_playback(
            session, boundary, trusted_scope=OwnerScope.personal("f03-test"), use_checkpoint=True
        )
        full = await load_playback(
            session, boundary, trusted_scope=OwnerScope.personal("f03-test"), use_checkpoint=False
        )
        assert checkpointed == full
        assert {item["reason"] for item in full.missing_intervals} == {
            "pre_journal_progress_unavailable",
            "retained_text_unavailable",
        }
        await session.rollback()


@pytest.mark.asyncio
async def test_missing_journal_suffix_is_reported_instead_of_silently_reduced():
    run_id = uuid4()
    async with execution_admin_session() as session:
        await write(session, run_id, 1)
        await write(session, run_id, 2, status="completed")
        missing = await write(session, run_id, 1, source_kind="progress")
        target = await write(session, run_id, 2, source_kind="progress")
        boundary = PlaybackBoundary(
            run_id=run_id,
            formal_position=target.formal_position,
            progress_position=target.progress_position,
            observed_order=target.observed_order,
            projection_revision=target.projection_revision,
            observed_at=target.observed_at,
            projector_version=1,
        )
        await session.execute(
            delete(ExecutionViewObservationORM).where(
                ExecutionViewObservationORM.run_id == run_id,
                ExecutionViewObservationORM.observed_order == missing.observed_order,
            )
        )
        checkpointed = await load_playback(
            session, boundary, trusted_scope=OwnerScope.personal("f03-test"), use_checkpoint=True
        )
        full = await load_playback(
            session, boundary, trusted_scope=OwnerScope.personal("f03-test"), use_checkpoint=False
        )
        assert checkpointed == full
        assert any(
            item["reason"] == "journal_observation_gap"
            and item["start_order"] == missing.observed_order
            and item["end_order"] == missing.observed_order
            for item in full.missing_intervals
        )
        await session.rollback()


@pytest.mark.asyncio
async def test_missing_journal_prefix_invalidates_checkpoint_state_for_equivalent_restore():
    run_id = uuid4()
    async with execution_admin_session() as session:
        await write(session, run_id, 1)
        missing = await write(
            session, run_id, 1, source_kind="progress", entity_id="missing-prefix-message"
        )
        await write(session, run_id, 2, status="completed")
        target = await write(
            session, run_id, 2, source_kind="progress", entity_id="retained-suffix-message"
        )
        boundary = PlaybackBoundary(
            run_id=run_id,
            formal_position=target.formal_position,
            progress_position=target.progress_position,
            observed_order=target.observed_order,
            projection_revision=target.projection_revision,
            observed_at=target.observed_at,
            projector_version=1,
        )
        await session.execute(
            delete(ExecutionViewObservationORM).where(
                ExecutionViewObservationORM.run_id == run_id,
                ExecutionViewObservationORM.observed_order == missing.observed_order,
            )
        )
        checkpointed = await load_playback(
            session, boundary, trusted_scope=OwnerScope.personal("f03-test"), use_checkpoint=True
        )
        full = await load_playback(
            session, boundary, trusted_scope=OwnerScope.personal("f03-test"), use_checkpoint=False
        )
        assert checkpointed == full
        assert "missing-prefix-message" not in checkpointed.state["message"]
        assert any(
            item["reason"] == "journal_observation_gap"
            and item["start_order"] == missing.observed_order
            for item in checkpointed.missing_intervals
        )
        await session.rollback()
