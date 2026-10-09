import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.application.execution.view_facts import ProjectionFact
from app.infrastructure.execution.postgres_view_observations import observe
from app.infrastructure.models.execution_view import (
    ExecutionRunViewORM,
    ExecutionViewObservationORM,
)
from tests.app.execution_test_support import execution_admin_session

pytestmark = pytest.mark.usefixtures("postgres_integration")


@pytest.mark.asyncio
async def test_observation_source_dedupe_and_rollback_are_atomic():
    run = uuid4()
    now = datetime.now(UTC)
    fact = ProjectionFact(10, 0, None, "run", str(run), {"status": "running"}, "formal")
    kwargs = {
        "run_id": run,
        "owner_user_id": "f02-test",
        "team_id": None,
        "family": "agent",
        "fact": fact,
        "source_identity": str(uuid4()),
        "event_id": None,
        "occurred_at": now,
    }
    async with execution_admin_session() as session:
        first = await observe(session, **kwargs)
        duplicate = await observe(session, **kwargs)
        assert first.observed_order == duplicate.observed_order == 1
        assert (await session.get(ExecutionRunViewORM, run)).projection_revision == 1
        await session.rollback()
    async with execution_admin_session() as session:
        assert await session.get(ExecutionRunViewORM, run) is None


@pytest.mark.asyncio
async def test_run_lock_serializes_committed_order():
    run = uuid4()
    now = datetime.now(UTC)
    kwargs = {
        "run_id": run,
        "owner_user_id": "f02-test",
        "team_id": None,
        "family": "agent",
        "event_id": None,
        "occurred_at": now,
    }
    fact = ProjectionFact(10, 0, None, "run", str(run), {"status": "running"}, "formal")
    async with execution_admin_session() as first:
        await observe(first, **kwargs, fact=fact, source_identity="a")
        entered = asyncio.Event()

        async def second():
            async with execution_admin_session() as session:
                entered.set()
                row = await observe(session, **kwargs, fact=fact, source_identity="b")
                await session.commit()
                return row.observed_order

        task = asyncio.create_task(second())
        await entered.wait()
        await asyncio.sleep(0.1)
        assert not task.done()
        await first.commit()
        assert await asyncio.wait_for(task, 5) == 2
    async with execution_admin_session() as session:
        rows = (
            await session.scalars(
                select(ExecutionViewObservationORM)
                .where(ExecutionViewObservationORM.run_id == run)
                .order_by(ExecutionViewObservationORM.observed_order)
            )
        ).all()
        assert [r.observed_order for r in rows] == [1, 2]


@pytest.mark.asyncio
async def test_formal_bundle_includes_run_and_progress_never_terminalizes():
    from app.domain.execution.run import RunFamily, RunState, RunStatus
    from app.infrastructure.execution.postgres_view_observations import observe_formal
    from tests.app.application.execution.test_view_facts import event

    run = uuid4()
    source = event("RunRetried", {}).model_copy(update={"stream_id": str(run), "event_id": uuid4()})
    state = RunState(run_id=run, family=RunFamily.AGENT, status=RunStatus.QUEUED)
    async with execution_admin_session() as session:
        row = await observe_formal(session, source, state)
        assert row is not None
        assert row.public_payload["facts"][0]["patch"]["status"] == "queued"
        fact = ProjectionFact(
            0,
            0,
            None,
            "step",
            "progress",
            {"progress": 100, "progress_status": "completed"},
            "progress",
        )
        await observe(
            session,
            run_id=run,
            owner_user_id="u",
            team_id=None,
            family="agent",
            fact=fact,
            source_identity="progress",
            event_id=None,
            occurred_at=datetime.now(UTC),
        )
        projected = await session.get(ExecutionRunViewORM, run)
        assert projected.status == "queued"
        assert projected.progress_position == 1
        await session.rollback()


@pytest.mark.asyncio
async def test_formal_projector_wires_observation_and_approval_run_state():
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from tests.app.application.execution.test_view_facts import event
    from tests.app.execution_test_support import run_policy_snapshot_json

    run = uuid4()
    source = event(
        "RunCreated",
        {
            "family": "agent",
            "source_entity_type": "session",
            "source_entity_id": "source",
            "parent_run_id": None,
        },
    ).model_copy(
        update={
            "stream_id": str(run),
            "event_id": uuid4(),
            "internal_payload": {
                "semantic_payload": {},
                "policy_snapshot": run_policy_snapshot_json("agent"),
            },
        }
    )
    projector = PostgresFormalProjector(
        session_factory=None, authorization=AuthorizationContext.system("f02")
    )
    async with execution_admin_session() as session:
        await projector._project_event(session, source, [], {})
        rows = (
            await session.scalars(
                select(ExecutionViewObservationORM).where(ExecutionViewObservationORM.run_id == run)
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].formal_position == source.position
        await session.rollback()


@pytest.mark.asyncio
async def test_progress_sink_commits_deduped_journal_and_ignores_late_phase():
    from uuid import UUID

    from app.application.execution.progress import ActivityProgressRecord
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_progress_sink import PostgresActivityProgressSink
    from tests.app.infrastructure.execution.test_snapshot_store import seed_stream

    stream, _ = await seed_stream(execution_admin_session)
    try:
        sink = PostgresActivityProgressSink(
            session_factory=execution_admin_session,
            authorization=AuthorizationContext.system("f02-progress"),
        )
        record = ActivityProgressRecord(
            run_id=UUID(stream.stream_id),
            activity_id=uuid4(),
            generation=0,
            claim_generation=2,
            sequence=2,
            kind="step",
            phase="late",
            progress=80,
            status="completed",
            message="password=hidden",
            owner_user_id="snapshot-user",
            team_id=None,
            occurred_at=datetime.now(UTC),
        )
        assert await sink.record(record)
        assert await sink.record(record)
        assert await sink.record(record.model_copy(update={"sequence": 1, "phase": "early"}))
        async with execution_admin_session() as session:
            rows = (
                await session.scalars(
                    select(ExecutionViewObservationORM)
                    .where(
                        ExecutionViewObservationORM.run_id == record.run_id,
                        ExecutionViewObservationORM.source_kind == "progress",
                    )
                    .order_by(ExecutionViewObservationORM.observed_order)
                )
            ).all()
            assert len(rows) == 2
            assert rows[0].progress_position == 1
            assert rows[1].progress_position == 2
            assert rows[1].public_payload["facts"] == []
            assert rows[1].public_payload["applied"] is False
            assert "hidden" not in str(rows[0].public_payload)
            run = await session.get(ExecutionRunViewORM, record.run_id)
            assert run.status == "queued"
            assert run.formal_position > 0
    finally:
        from app.domain.models.scope import OwnerScope
        from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector

        projector = PostgresFormalProjector(
            session_factory=execution_admin_session,
            authorization=AuthorizationContext.system("f02-test-drain"),
        )
        while (
            await projector.run_once(OwnerScope.personal("snapshot-user"), limit=1000)
        ).processed:
            pass


@pytest.mark.asyncio
async def test_claim_attempt_replaces_only_request_placeholder_and_keeps_unknown_start_open():
    from app.application.execution.view_facts import attempt_key, request_key
    from app.domain.execution.run import RunFamily, RunState, RunStatus
    from app.infrastructure.execution.postgres_view_observations import observe_formal
    from app.infrastructure.models.execution_view import ExecutionStepViewORM
    from tests.app.application.execution.test_view_facts import event

    run, activity = uuid4(), uuid4()
    state = RunState(run_id=run, family=RunFamily.AGENT, status=RunStatus.RUNNING)
    async with execution_admin_session() as session:

        async def write(name, payload):
            source = event(
                name, {"activity_id": str(activity), "generation": 0, **payload}
            ).model_copy(update={"stream_id": str(run), "event_id": uuid4()})
            return await observe_formal(session, source, state)

        await write(
            "ActivityRequested", {"activity_type": "tool.call", "public_data": {"name": "search"}}
        )
        started = await write("ActivityCallStarted", {"claim_generation": 1})
        assert any(f["patch"].get("removed") for f in started.public_payload["facts"])
        first = await session.scalar(
            select(ExecutionStepViewORM).where(
                ExecutionStepViewORM.run_id == run,
                ExecutionStepViewORM.step_id == attempt_key(str(activity), 0, 1),
            )
        )
        assert first.kind == "tool"
        assert first.tool_name == "search"
        assert (
            await session.scalar(
                select(ExecutionStepViewORM).where(
                    ExecutionStepViewORM.run_id == run,
                    ExecutionStepViewORM.step_id == request_key(str(activity)),
                )
            )
            is None
        )
        await write(
            "ActivityOutcomeUnknown",
            {"claim_generation": 2, "failure_code": "CALL_OUTCOME_UNKNOWN"},
        )
        assert first.status == "running"
        assert first.ended_at is None
        unmatched = await session.scalar(
            select(ExecutionStepViewORM).where(
                ExecutionStepViewORM.run_id == run,
                ExecutionStepViewORM.step_id == attempt_key(str(activity), 0, 2),
            )
        )
        assert unmatched.started_at is None
        assert unmatched.duration_ms is None
        await session.rollback()


@pytest.mark.asyncio
async def test_rollback_releases_waiter_without_allocating_a_hole():
    run = uuid4()
    kwargs = {
        "run_id": run,
        "owner_user_id": "f02-test",
        "team_id": None,
        "family": "agent",
        "event_id": None,
        "occurred_at": datetime.now(UTC),
    }
    fact = ProjectionFact(10, 0, None, "run", str(run), {"status": "running"}, "formal")
    async with execution_admin_session() as first:
        await observe(first, **kwargs, fact=fact, source_identity="rolled-back")
        entered = asyncio.Event()

        async def later():
            async with execution_admin_session() as session:
                entered.set()
                row = await observe(session, **kwargs, fact=fact, source_identity="committed")
                await session.commit()
                return row.observed_order

        task = asyncio.create_task(later())
        await entered.wait()
        await asyncio.sleep(0.1)
        assert not task.done()
        await first.rollback()
        assert await asyncio.wait_for(task, 5) == 1


@pytest.mark.asyncio
async def test_duplicate_source_cannot_reapply_older_run_state():
    run = uuid4()
    kwargs = {
        "run_id": run,
        "owner_user_id": "f02-test",
        "team_id": None,
        "family": "agent",
        "event_id": None,
        "occurred_at": datetime.now(UTC),
    }
    started = ProjectionFact(10, 0, None, "run", str(run), {"status": "running"}, "formal")
    terminal = ProjectionFact(11, 0, None, "run", str(run), {"status": "completed"}, "formal")
    async with execution_admin_session() as session:
        await observe(session, **kwargs, fact=started, source_identity="started")
        await observe(session, **kwargs, fact=terminal, source_identity="terminal")
        repeated = await observe(session, **kwargs, fact=started, source_identity="started")
        row = await session.get(ExecutionRunViewORM, run)
        assert repeated.observed_order == 1
        assert row.observed_order == 2
        assert row.status == "completed"
        await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("claim_generation", [1, None], ids=["actual-claim", "legacy-unknown"])
@pytest.mark.parametrize(
    "settlement", ["ActivityCompleted", "ActivityFailed", "ActivityOutcomeUnknown"]
)
async def test_linked_tool_keeps_parent_and_invocation_through_same_attempt_settlement(
    claim_generation, settlement
):
    from app.application.execution.view_facts import attempt_key, request_key
    from app.domain.execution.run import RunFamily, RunState, RunStatus
    from app.infrastructure.execution.postgres_view_observations import observe_formal
    from app.infrastructure.models.execution_view import ExecutionStepViewORM
    from tests.app.application.execution.test_view_facts import event

    run, model, tool, invocation = uuid4(), uuid4(), uuid4(), uuid4()
    state = RunState(run_id=run, family=RunFamily.AGENT, status=RunStatus.RUNNING)
    async with execution_admin_session() as session:

        async def write(name, activity, payload):
            source = event(
                name, {"activity_id": str(activity), "generation": 0, **payload}, 2
            ).model_copy(update={"stream_id": str(run), "event_id": uuid4()})
            return await observe_formal(session, source, state)

        await write("ActivityRequested", model, {"activity_type": "model.call"})
        await write("ActivityCallStarted", model, {"claim_generation": 1})
        await write("ActivityCompleted", model, {"claim_generation": 1})
        parent = attempt_key(str(model), 0, 1)
        await write(
            "ActivityRequested",
            tool,
            {
                "activity_type": "tool.call",
                "parent_activity_id": str(model),
                "invocation_id": str(invocation),
                "public_data": {"name": "search"},
            },
        )
        started = await write("ActivityCallStarted", tool, {"claim_generation": claim_generation})
        step_id = (
            attempt_key(str(tool), 0, claim_generation)
            if claim_generation
            else request_key(str(tool))
        )

        def assert_link(row):
            patch = next(
                f["patch"]
                for f in row.public_payload["facts"]
                if f["kind"] == "step" and f["id"] == step_id
            )
            assert patch["parent_step_id"] == parent
            assert patch["relationship"] == "direct"
            assert patch["invocation_id"] == str(invocation)
            assert "parent_step_id" not in patch["completeness"]["missing_fields"]
            assert "invocation_id" not in patch["completeness"]["missing_fields"]

        assert_link(started)
        ended = await write(
            settlement,
            tool,
            {
                "claim_generation": claim_generation,
                **({"failure_code": "FAILED"} if settlement != "ActivityCompleted" else {}),
            },
        )
        assert_link(ended)
        live = await session.scalar(
            select(ExecutionStepViewORM).where(
                ExecutionStepViewORM.run_id == run, ExecutionStepViewORM.step_id == step_id
            )
        )
        assert live.parent_step_id == parent
        assert live.relationship == "direct"
        assert live.invocation_id == invocation
        assert live.kind == "tool"
        assert live.tool_name == "search"
        assert (
            live.status
            == {
                "ActivityCompleted": "completed",
                "ActivityFailed": "failed",
                "ActivityOutcomeUnknown": "unknown",
            }[settlement]
        )
        await session.rollback()
