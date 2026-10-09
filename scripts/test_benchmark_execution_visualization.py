"""Offline capacity construction checks; these are NOT capacity measurements."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from scripts.seed_execution_visualization import event_count, run_identity, run_started_at


def test_standard_fixture_totals_ten_million_events():
    assert sum(event_count(i) for i in range(100_000)) == 10_000_000
    assert sum(event_count(i) == 10_000 for i in range(100_000)) == 10


@pytest.mark.parametrize("index", [-1, 100_000, True, 1.5])
def test_distribution_rejects_outside_standard_fixture(index):
    with pytest.raises(ValueError, match="outside standard fixture"):
        event_count(index)


def test_fixture_id_seed_and_index_bind_identity():
    fixture = UUID(int=1)
    assert run_identity(fixture, 7, 1000) == run_identity(fixture, 7, 1000)
    assert len({run_identity(fixture, 7, i) for i in range(100_000)}) == 100_000
    assert run_identity(fixture, 8, 1000) != run_identity(fixture, 7, 1000)
    assert run_identity(UUID(int=2), 7, 1000) != run_identity(fixture, 7, 1000)


def test_seed_dates_cover_ninety_days_and_keep_hot_runs_live():
    end = datetime(2026, 9, 17, tzinfo=UTC)
    dates = [run_started_at(i, end) for i in range(100_000)]
    assert min(dates) == end - timedelta(days=90)
    assert max(dates) < end
    assert all(end - timedelta(seconds=20) <= dates[i] for i in range(10))
    with pytest.raises(ValueError, match="timezone"):
        run_started_at(0, end.replace(tzinfo=None))


def test_planner_requires_real_claim_and_outcome_and_rejects_foreign_identity():
    from scripts.execution_capacity.commands import RunPlan

    from app.domain.execution.activity import ActivityClaim, ActivityOutcome, ActivityRequest

    plan = _plan(1000)
    steps = iter(plan.commands())
    next(steps)
    next(steps)
    request = next(steps).bind()
    start = next(steps)
    complete = next(steps)
    with pytest.raises(ValueError, match="claim"):
        start.bind()
    claim = ActivityClaim(
        request=ActivityRequest(
            activity_id=request.payload["activity_id"],
            activity_type="tool.call",
            aggregate_type="run",
            aggregate_id=str(plan.run_id),
            generation=0,
            timeout_at=request.payload["timeout_at"],
            input_ref=None,
            input_digest=request.payload["input_digest"],
            input_payload=request.payload["input_payload"],
        ),
        claim_generation=7,
        owner_user_id="capacity-test-owner",
        team_id=None,
    )
    assert start.bind(claim=claim).payload["claim_generation"] == 7
    with pytest.raises(ValueError, match="outcome"):
        complete.bind(claim=claim)
    assert (
        complete.bind(claim=claim, outcome=ActivityOutcome(status="succeeded")).payload[
            "claim_generation"
        ]
        == 7
    )
    with pytest.raises(ValueError, match="claim"):
        start.bind(claim=claim.model_copy(update={"owner_user_id": "foreign"}))
    with pytest.raises(ValueError, match="claim"):
        start.bind(
            claim=claim.model_copy(
                update={"request": claim.request.model_copy(update={"input_digest": "changed"})}
            )
        )
    with pytest.raises(ValueError, match="outcome"):
        complete.bind(
            claim=claim, outcome=ActivityOutcome(status="unknown", failure_code="unresolved")
        )
    assert isinstance(plan, RunPlan)


def _plan(index):
    from scripts.execution_capacity.commands import RunPlan

    from app.domain.execution.family import RunFamily
    from app.domain.runtime_policy import (
        ActiveExecutionPolicy,
        ExecutionPolicy,
        ExecutionPolicyRevision,
        RuntimePolicyHead,
        derive_run_policy_snapshot,
        policy_digest,
    )

    now = datetime(2026, 9, 17, tzinfo=UTC)
    policy = ExecutionPolicy()
    active = ActiveExecutionPolicy(
        head=RuntimePolicyHead(
            version=1,
            execution_revision_id=UUID(int=90),
            operations_revision_id=UUID(int=91),
            updated_by="unit",
            updated_at=now,
        ),
        revision=ExecutionPolicyRevision(
            id=UUID(int=90),
            sequence=1,
            schema_version=1,
            policy=policy,
            digest=policy_digest(1, policy),
            created_by="unit",
            note="unit",
            created_at=now,
        ),
    )
    return RunPlan(
        fixture_id=UUID(int=1),
        seed=7,
        index=index,
        window_end=now,
        owner_user_id="capacity-test-owner",
        team_id=None,
        policy=derive_run_policy_snapshot(active, RunFamily.AGENT),
        activity_type="tool.call",
        activity_input={"unit": "no execution"},
    )


@pytest.mark.parametrize(
    ("index", "expected_status", "activities"),
    [(1000, "completed", 32), (10, "completed", 31), (0, "completed", 3331)],
)
def test_command_plan_produces_exact_legal_hash_verified_replay(index, expected_status, activities):
    from app.domain.execution.activity import ActivityClaim, ActivityOutcome
    from app.domain.execution.aggregate import replay
    from app.domain.execution.events import StoredEvent
    from app.domain.execution.run import RunAggregate
    from app.domain.execution.store import calculate_event_hash, verify_stream

    plan = _plan(index)
    aggregate = RunAggregate()
    state = aggregate.initial_state(str(plan.run_id))
    events, claims = [], {}
    request_count = 0
    for step in plan.commands():
        claim = claims.get(step.activity_id)
        command = step.bind(
            claim=claim,
            outcome=ActivityOutcome(status="succeeded")
            if step.command_type == "CompleteActivity"
            else None,
        )
        decision = aggregate.decide(state, command)
        assert len(decision.events) == 1
        for request in decision.activity_requests:
            request_count += 1
            claims[request.activity_id] = ActivityClaim(
                request=request,
                claim_generation=7,
                owner_user_id=plan.owner_user_id,
                team_id=plan.team_id,
            )
            assert len(decision.scheduled_commands) == 1
        event = StoredEvent(
            **decision.events[0].model_dump(),
            position=len(events) + 1,
            event_id=UUID(int=len(events) + 1),
            stream_type="run",
            stream_id=str(plan.run_id),
            stream_version=len(events) + 1,
            owner_user_id=plan.owner_user_id,
            team_id=plan.team_id,
            correlation_id=command.correlation_id,
            causation_id=command.command_id,
            occurred_at=command.issued_at,
            prev_hash=events[-1].event_hash if events else "0" * 64,
            event_hash="0" * 64,
        )
        event = event.model_copy(update={"event_hash": calculate_event_hash(event)})
        events.append(event)
        state = aggregate.evolve(state, event)
    assert len(events) == event_count(index)
    assert request_count == activities
    assert state.status == expected_status
    assert not state.active_activity_ids
    assert events[-1].occurred_at < plan.window_end
    verify_stream(events)
    assert replay(aggregate, events).state == state
