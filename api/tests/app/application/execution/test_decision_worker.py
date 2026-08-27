"""Automatic workflow progression from formal Run projections."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.application.execution.decision_worker import (
    DecisionCandidate,
    DecisionWorker,
)
from app.application.execution.orchestrator import CommandResult
from app.domain.execution.run import RunFamily, RunState, RunStatus
from app.domain.runtime_policy import ActivityExecutionPolicy, ExecutionPolicy
from tests.app.execution_test_support import run_policy_snapshot_json

NOW = datetime(2026, 8, 24, 13, tzinfo=UTC)
RUN_ID = UUID("60000000-0000-0000-0000-000000000001")


class Source:
    def __init__(self, state: RunState | None = None) -> None:
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
