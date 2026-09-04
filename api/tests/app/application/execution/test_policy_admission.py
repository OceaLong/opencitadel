from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.execution.admission import RunAdmissionService
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.run import RunAggregate, RunFamily
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import (
    ActiveExecutionPolicy,
    ActivityExecutionPolicy,
    AgentExecutionPolicy,
    ExecutionPolicy,
    ExecutionPolicyRevision,
    RuntimePolicyHead,
    policy_digest,
)

NOW = datetime(2026, 8, 26, 2, 0, tzinfo=UTC)


class _Objects:
    def __init__(self) -> None:
        self.payloads = []

    async def put_input(self, run_id, payload):
        del run_id
        self.payloads.append(payload)
        return "object://input", "sha256:" + "1" * 64


class _Commands:
    def __init__(self) -> None:
        self.commands = []
        self.contexts = []

    async def submit(self, command, context, *, sink=None) -> None:
        del sink
        self.commands.append(command)
        self.contexts.append(context)


class _PolicyHeads:
    def __init__(self, active: ActiveExecutionPolicy) -> None:
        self.active = active
        self.calls = []

    async def active_execution(self, *, require_fresh, now):
        self.calls.append((require_fresh, now))
        return self.active


def _active_execution() -> ActiveExecutionPolicy:
    policy = ExecutionPolicy(
        agent=AgentExecutionPolicy(max_iterations=8, max_retries=4),
        activity=ActivityExecutionPolicy(
            tool_timeout_seconds=17,
            mcp_connect_timeout_seconds=6,
        ),
    )
    execution_id = uuid4()
    operations_id = uuid4()
    head = RuntimePolicyHead(
        version=3,
        execution_revision_id=execution_id,
        operations_revision_id=operations_id,
        updated_by="admin-1",
        updated_at=NOW,
    )
    return ActiveExecutionPolicy(
        head=head,
        revision=ExecutionPolicyRevision(
            id=execution_id,
            sequence=3,
            schema_version=1,
            policy=policy,
            digest=policy_digest(1, policy),
            created_by="admin-1",
            note="admission test",
            created_at=NOW,
        ),
    )


@pytest.mark.asyncio
async def test_admission_injects_policy_snapshot_not_caller_input() -> None:
    commands = _Commands()
    policy_heads = _PolicyHeads(_active_execution())
    objects = _Objects()
    admission = RunAdmissionService(
        command_ingress=commands,
        activity_objects=objects,
        policy_heads=policy_heads,
        clock=lambda: NOW,
    )

    await admission.admit(
        family=RunFamily.AGENT,
        source_entity_type="session",
        source_entity_id="session-1",
        owner_scope=OwnerScope.personal("user-1"),
        private_input={"policy_snapshot": "caller-controlled"},
        public_input={"message": "hello"},
    )

    command = commands.commands[0]
    snapshot = command.payload["policy_snapshot"]
    assert snapshot["family"] == "agent"
    assert snapshot["snapshot_digest"].startswith("sha256:")
    assert snapshot["execution_revision_id"] == str(policy_heads.active.revision.id)
    assert "timeout_seconds" not in command.payload["semantic_payload"]
    assert "max_retries" not in command.payload["semantic_payload"]
    # Bind to the registry, not a literal: the orchestrator rejects any
    # submission that is not the latest registered version, so a pinned number
    # here would go green while every real CreateRun is refused (the exact
    # regression the 2026-09 baseline reset shipped once).
    assert command.command_schema_version == RunAggregate().command_registry.latest_version(
        "CreateRun"
    )
    # And the payload must actually parse at that version.
    RunAggregate().decide(
        RunAggregate().initial_state(str(command.run_id)),
        CommandEnvelope(
            command_id=command.command_id,
            command_type=command.command_type,
            command_schema_version=command.command_schema_version,
            stream_type="run",
            stream_id=str(command.run_id),
            expected_stream_version=0,
            owner_user_id="user-1",
            team_id=None,
            correlation_id=command.command_id,
            causation_id=None,
            issued_at=NOW,
            payload=command.payload,
        ),
    )
    assert policy_heads.calls == [(True, NOW)]
    assert objects.payloads == [{"policy_snapshot": "caller-controlled"}]


@pytest.mark.asyncio
async def test_private_input_factory_uses_the_same_policy_as_the_run_snapshot() -> None:
    commands = _Commands()
    active = _active_execution()
    policy_heads = _PolicyHeads(active)
    objects = _Objects()
    admission = RunAdmissionService(
        command_ingress=commands,
        activity_objects=objects,
        policy_heads=policy_heads,
        clock=lambda: NOW,
    )
    seen = []

    async def private_input(policy: ExecutionPolicy):
        seen.append(policy)
        return {"max_iterations": policy.agent.max_iterations}

    await admission.admit(
        family=RunFamily.AGENT,
        source_entity_type="session",
        source_entity_id="session-1",
        owner_scope=OwnerScope.personal("user-1"),
        private_input=None,
        private_input_factory=private_input,
        public_input={"message": "hello"},
    )

    assert seen == [active.revision.policy]
    assert objects.payloads == [{"max_iterations": 8}]
    assert commands.commands[0].payload["policy_snapshot"]["family_policy"]["agent"] == {
        "max_iterations": 8,
        "max_retries": 4,
    }
    assert policy_heads.calls == [(True, NOW)]


class _ActiveRunCounter:
    def __init__(self, active: int) -> None:
        self.active = active
        self.scopes = []

    async def count_active_runs(self, *, owner_scope):
        self.scopes.append(owner_scope)
        return self.active


@pytest.mark.asyncio
async def test_admission_refuses_runs_beyond_the_per_scope_active_ceiling() -> None:
    """K2-8: per-scope backpressure — at the ceiling, admit() raises
    ADMISSION_LIMIT_EXCEEDED before writing anything."""
    from app.application.execution.admission import AdmissionLimitExceededError

    commands = _Commands()
    objects = _Objects()
    counter = _ActiveRunCounter(active=200)
    admission = RunAdmissionService(
        command_ingress=commands,
        activity_objects=objects,
        policy_heads=_PolicyHeads(_active_execution()),
        active_run_counter=counter,
        max_active_runs_per_scope=200,
        clock=lambda: NOW,
    )

    with pytest.raises(AdmissionLimitExceededError, match="ADMISSION_LIMIT_EXCEEDED"):
        await admission.admit(
            family=RunFamily.AGENT,
            source_entity_type="session",
            source_entity_id="session-1",
            owner_scope=OwnerScope.personal("user-1"),
            private_input={"message": "hello"},
            public_input={"message": "hello"},
        )

    assert commands.commands == []  # nothing enqueued
    assert objects.payloads == []  # nothing persisted to object storage
    assert counter.scopes[0].user_id == "user-1"


@pytest.mark.asyncio
async def test_admission_below_the_ceiling_and_with_zero_limit_admits_normally() -> None:
    commands = _Commands()
    counter = _ActiveRunCounter(active=199)
    admission = RunAdmissionService(
        command_ingress=commands,
        activity_objects=_Objects(),
        policy_heads=_PolicyHeads(_active_execution()),
        active_run_counter=counter,
        max_active_runs_per_scope=200,
        clock=lambda: NOW,
    )
    await admission.admit(
        family=RunFamily.AGENT,
        source_entity_type="session",
        source_entity_id="session-1",
        owner_scope=OwnerScope.personal("user-1"),
        private_input={"message": "hello"},
        public_input={"message": "hello"},
    )
    assert len(commands.commands) == 1

    # limit=0 disables the gate entirely: the counter is never consulted.
    unlimited_counter = _ActiveRunCounter(active=10_000)
    unlimited = RunAdmissionService(
        command_ingress=commands,
        activity_objects=_Objects(),
        policy_heads=_PolicyHeads(_active_execution()),
        active_run_counter=unlimited_counter,
        max_active_runs_per_scope=0,
        clock=lambda: NOW,
    )
    await unlimited.admit(
        family=RunFamily.AGENT,
        source_entity_type="session",
        source_entity_id="session-1",
        owner_scope=OwnerScope.personal("user-1"),
        private_input={"message": "hello"},
        public_input={"message": "hello"},
    )
    assert len(commands.commands) == 2
    assert unlimited_counter.scopes == []
