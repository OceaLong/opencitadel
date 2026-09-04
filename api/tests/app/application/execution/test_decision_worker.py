"""Automatic workflow progression from formal Run projections."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.application.execution.decision_worker import (
    DecisionCandidate,
    DecisionWorker,
)
from app.application.execution.decisions.base import activity_identity
from app.application.execution.orchestrator import CommandResult
from app.domain.execution.run import RunFamily, RunState, RunStatus
from app.domain.runtime_policy import ActivityExecutionPolicy, ExecutionPolicy
from tests.app.execution_test_support import run_policy_snapshot_json

NOW = datetime(2026, 8, 24, 13, tzinfo=UTC)
RUN_ID = UUID("60000000-0000-0000-0000-000000000001")


class Source:
    def __init__(self, state: RunState | None = None) -> None:
        self.disarmed: list = []
        self.state = state or RunState(
            run_id=RUN_ID,
            family=RunFamily.ASK,
            source_entity_type="session",
            source_entity_id="session-1",
            semantic_payload={
                "retrieval_required": False,
                "timeout_seconds": 30,
                "input_ref": None,
                "input_digest": "a" * 64,
            },
            policy_snapshot=run_policy_snapshot_json("ask"),
            status=RunStatus.QUEUED,
            stream_version=1,
            owner_user_id="user-1",
            correlation_id=UUID(int=9),
        )

    async def load_ready(self, *, limit):
        del limit
        return (DecisionCandidate(state=self.state),)

    async def disarm(self, run_ids):
        self.disarmed = list(run_ids)


class Service:
    def __init__(self):
        self.commands = []

    async def submit(self, command, context):
        self.commands.append((command, context))
        return CommandResult(
            command_id=command.command_id,
            status="accepted",
            first_event_position=1,
            last_event_position=1,
            rejection_code=None,
        )


@pytest.mark.asyncio
async def test_worker_submits_exactly_one_next_command_per_ready_run() -> None:
    service = Service()
    worker = DecisionWorker(source=Source(), run_service=service)

    stats = await worker.run_once(now=NOW, limit=10)

    assert stats.loaded == 1
    assert stats.submitted == 1
    assert service.commands[0][0].command_type == "StartRun"
    assert service.commands[0][1].owner_user_id == "user-1"


@pytest.mark.asyncio
async def test_worker_passes_validated_context_and_uses_snapshot_timeout() -> None:
    policy = ExecutionPolicy(
        activity=ActivityExecutionPolicy(
            tool_timeout_seconds=17,
            mcp_connect_timeout_seconds=4,
        )
    )
    state = RunState(
        run_id=RUN_ID,
        family=RunFamily.ASK,
        source_entity_type="session",
        source_entity_id="session-1",
        semantic_payload={
            "retrieval_required": False,
            "timeout_seconds": 999,
            "input_ref": None,
            "input_digest": "a" * 64,
        },
        policy_snapshot=run_policy_snapshot_json("ask", policy=policy),
        status=RunStatus.RUNNING,
        stream_version=2,
        owner_user_id="user-1",
        correlation_id=UUID(int=9),
    )
    service = Service()
    worker = DecisionWorker(source=Source(state), run_service=service)

    await worker.run_once(now=NOW, limit=1)

    assert service.commands[0][0].payload["timeout_at"] == (NOW + timedelta(seconds=17)).isoformat()


@pytest.mark.asyncio
async def test_worker_injects_operations_approval_ttl(monkeypatch) -> None:
    """RequestApproval commands must carry the tenant's approval TTL, not rely
    on the aggregate's fixed default — otherwise the control-plane knob is dead."""
    from app.application.execution import decision_worker as module
    from app.domain.execution.commands import RegisteredCommand

    approval_command = RegisteredCommand(
        command_id=UUID(int=5),
        command_type="RequestApproval",
        run_id=RUN_ID,
        payload={
            "approval_id": str(UUID(int=6)),
            "subject_activity_id": str(UUID(int=7)),
            "approval_kind": "tool_effect",
            "risk_summary": "risk",
            "subject_label": "tool",
        },
    )
    monkeypatch.setattr(module, "next_command", lambda *args, **kwargs: approval_command)
    service = Service()
    ttl_calls = []

    async def approval_ttl(now):
        ttl_calls.append(now)
        return 30

    worker = DecisionWorker(
        source=Source(),
        run_service=service,
        approval_ttl_minutes=approval_ttl,
    )
    await worker.run_once(now=NOW, limit=1)

    assert ttl_calls == [NOW]
    assert service.commands[0][0].payload["ttl_minutes"] == 30


@pytest.mark.asyncio
async def test_worker_leaves_non_approval_commands_untouched() -> None:
    service = Service()

    async def approval_ttl(now):
        raise AssertionError("must not fetch TTL for non-approval commands")

    worker = DecisionWorker(
        source=Source(),
        run_service=service,
        approval_ttl_minutes=approval_ttl,
    )
    await worker.run_once(now=NOW, limit=1)

    assert "ttl_minutes" not in service.commands[0][0].payload


@pytest.mark.asyncio
async def test_idle_runs_are_disarmed_so_they_stop_being_reloaded() -> None:
    """K2-1: a Run the planner has nothing to do for (e.g. WAITING on approval)
    is disarmed at the source, so its projection row leaves the ready scan."""
    waiting = RunState(
        run_id=RUN_ID,
        family=RunFamily.ASK,
        source_entity_type="session",
        source_entity_id="session-1",
        semantic_payload={
            "retrieval_required": False,
            "timeout_seconds": 30,
            "input_ref": None,
            "input_digest": "a" * 64,
        },
        policy_snapshot=run_policy_snapshot_json("ask"),
        status=RunStatus.WAITING,
        wait_reason="approval",
        stream_version=3,
        owner_user_id="user-1",
        correlation_id=UUID(int=9),
    )
    source = Source(waiting)
    service = Service()
    worker = DecisionWorker(source=source, run_service=service)

    stats = await worker.run_once(now=NOW, limit=10)

    assert stats.idle == 1
    assert service.commands == []
    assert source.disarmed == [RUN_ID]


@pytest.mark.asyncio
async def test_waiting_retry_is_idle_for_the_decision_loop() -> None:
    """K2-4: retries are timer-driven — the decision loop must NOT submit
    RetryRun for a WAITING(retry) Run (the old zero-delay retry storm)."""
    retry_waiting = RunState(
        run_id=RUN_ID,
        family=RunFamily.ASK,
        source_entity_type="session",
        source_entity_id="session-1",
        semantic_payload={
            "retrieval_required": False,
            "timeout_seconds": 30,
            "input_ref": None,
            "input_digest": "a" * 64,
        },
        policy_snapshot=run_policy_snapshot_json("ask"),
        status=RunStatus.WAITING,
        wait_reason="retry",
        failure_code="PROVIDER_TIMEOUT",
        stream_version=4,
        owner_user_id="user-1",
        correlation_id=UUID(int=9),
    )
    source = Source(retry_waiting)
    service = Service()
    worker = DecisionWorker(source=source, run_service=service)

    stats = await worker.run_once(now=NOW, limit=10)

    assert stats.idle == 1
    assert service.commands == []
    assert source.disarmed == [RUN_ID]


class _TwoCandidateSource(Source):
    """First candidate's planner errors (digest without payload); second is fine."""

    def __init__(self, broken: RunState, healthy: RunState) -> None:
        super().__init__(healthy)
        self.broken = broken

    async def load_ready(self, *, limit):
        del limit
        return (
            DecisionCandidate(state=self.broken),
            DecisionCandidate(state=self.state),
        )


@pytest.mark.asyncio
async def test_one_runs_planner_defect_does_not_abort_the_batch() -> None:
    """The exact production wedge: a model activity settled with a decision
    digest but the planner received no payload — the raised ValueError must be
    isolated per Run (logged, counted, disarmed), not kill the decisions lane.
    """
    broken_base = RunState(
        run_id=UUID("60000000-0000-0000-0000-0000000000bb"),
        family=RunFamily.ASK,
        source_entity_type="session",
        source_entity_id="session-broken",
        semantic_payload={"input_ref": None, "input_digest": "a" * 64},
        policy_snapshot=run_policy_snapshot_json("ask"),
        status=RunStatus.RUNNING,
        stream_version=9,
        owner_user_id="user-1",
        correlation_id=UUID(int=8),
    )
    retrieval_id = activity_identity(broken_base, "retrieval:0")
    model_id = activity_identity(broken_base, "model:0")
    broken = broken_base.model_copy(
        update={
            "settled_activities": (
                (retrieval_id, "succeeded", 0),
                (model_id, "succeeded", 0),
            ),
            "activity_results": (
                (retrieval_id, 0, "result://retrieval", None, None),
                (model_id, 0, "result://model", None, "sha256:" + "ab" * 32),
            ),
        }
    )
    # Force the ask planner down the model-result path: retrieval settled, so
    # activity_result() runs against the digest with an empty outcomes map.
    source = _TwoCandidateSource(broken, Source().state)
    service = Service()
    worker = DecisionWorker(source=source, run_service=service)

    stats = await worker.run_once(now=NOW, limit=10)

    # The healthy queued Run still got its StartRun.
    assert stats.submitted == 1
    assert stats.errors == 1
    assert service.commands[0][0].command_type == "StartRun"
    # The broken Run is disarmed so it is not hot-retried every poll.
    assert broken.run_id in source.disarmed
