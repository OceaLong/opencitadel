"""Pure contracts for the production universal Run aggregate."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.application.execution.decisions.base import result_refs
from app.domain.execution.aggregate import ReplaySnapshot, replay
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.events import NewEvent, StoredEvent
from app.domain.execution.run import (
    InvalidRunTransitionError,
    RunAggregate,
    RunFamily,
    RunState,
    UnknownRunCommandError,
    decision_data_digest,
)
from app.domain.runtime_policy import (
    ActiveExecutionPolicy,
    ExecutionPolicy,
    ExecutionPolicyRevision,
    RuntimePolicyHead,
    derive_run_policy_snapshot,
    policy_digest,
)
from app.domain.runtime_policy.errors import RuntimePolicyIntegrityError

NOW = datetime(2026, 8, 24, 8, tzinfo=UTC)
RUN_ID = UUID("10000000-0000-0000-0000-000000000001")
PARENT_ID = UUID("10000000-0000-0000-0000-000000000002")
CORRELATION_ID = UUID("10000000-0000-0000-0000-000000000003")
EXECUTION_REVISION_ID = UUID("10000000-0000-0000-0000-000000000004")
OPERATIONS_REVISION_ID = UUID("10000000-0000-0000-0000-000000000005")


def policy_snapshot(family: RunFamily) -> dict:
    policy = ExecutionPolicy()
    active = ActiveExecutionPolicy(
        head=RuntimePolicyHead(
            version=1,
            execution_revision_id=EXECUTION_REVISION_ID,
            operations_revision_id=OPERATIONS_REVISION_ID,
            updated_by="test",
            updated_at=NOW,
        ),
        revision=ExecutionPolicyRevision(
            id=EXECUTION_REVISION_ID,
            sequence=1,
            schema_version=1,
            policy=policy,
            digest=policy_digest(1, policy),
            created_by="test",
            note="universal Run test",
            created_at=NOW,
        ),
    )
    return derive_run_policy_snapshot(active, family).model_dump(mode="json")


def command(
    command_type: str,
    version: int,
    payload: dict | None = None,
) -> CommandEnvelope:
    resolved_payload = dict(payload or {})
    if command_type == "CreateRun" and "family" in resolved_payload:
        resolved_payload.setdefault(
            "policy_snapshot",
            policy_snapshot(RunFamily(str(resolved_payload["family"]))),
        )
    return CommandEnvelope(
        command_id=uuid4(),
        command_type=command_type,
        command_schema_version=1,
        stream_type="run",
        stream_id=str(RUN_ID),
        expected_stream_version=version,
        owner_user_id="user-1",
        team_id=None,
        correlation_id=CORRELATION_ID,
        causation_id=None,
        issued_at=NOW,
        payload=resolved_payload,
    )


def test_snapshotless_create_run_is_rejected() -> None:
    aggregate = RunAggregate()
    snapshotless = CommandEnvelope(
        command_id=uuid4(),
        command_type="CreateRun",
        command_schema_version=1,
        stream_type="run",
        stream_id=str(RUN_ID),
        expected_stream_version=0,
        owner_user_id="user-1",
        team_id=None,
        correlation_id=CORRELATION_ID,
        causation_id=None,
        issued_at=NOW,
        payload={
            "family": "agent",
            "source_entity_type": "session",
            "source_entity_id": "session-1",
            "semantic_payload": {},
            "public_input": {},
        },
    )

    with pytest.raises(ValueError, match="policy_snapshot"):
        aggregate.decide(aggregate.initial_state(str(RUN_ID)), snapshotless)


def test_snapshotless_run_created_event_is_rejected() -> None:
    aggregate = RunAggregate()
    event = StoredEvent(
        position=1,
        event_id=UUID(int=1),
        stream_type="run",
        stream_id=str(RUN_ID),
        stream_version=1,
        event_type="RunCreated",
        event_schema_version=1,
        public_payload={
            "family": "agent",
            "source_entity_type": "session",
            "source_entity_id": "session-1",
            "parent_run_id": None,
            "input": {},
        },
        internal_payload={"semantic_payload": {}},
        secret_ref=None,
        owner_user_id="user-1",
        team_id=None,
        correlation_id=CORRELATION_ID,
        causation_id=None,
        occurred_at=NOW,
        prev_hash="0" * 64,
        event_hash="1" * 64,
    )

    with pytest.raises(RuntimePolicyIntegrityError, match="snapshot"):
        aggregate.evolve(aggregate.initial_state(str(RUN_ID)), event)


def materialize(
    proposed: tuple[NewEvent, ...],
    existing: list[StoredEvent],
) -> None:
    for item in proposed:
        version = len(existing) + 1
        existing.append(
            StoredEvent(
                position=version,
                event_id=UUID(int=version),
                stream_type="run",
                stream_id=str(RUN_ID),
                stream_version=version,
                event_type=item.event_type,
                event_schema_version=item.event_schema_version,
                public_payload=item.public_payload,
                internal_payload=item.internal_payload,
                secret_ref=item.secret_ref,
                owner_user_id="user-1",
                team_id=None,
                correlation_id=CORRELATION_ID,
                causation_id=UUID(int=100 + version),
                occurred_at=NOW + timedelta(seconds=version),
                prev_hash="0" * 64,
                event_hash=f"{version:064x}",
            )
        )


def decide_and_replay(
    aggregate: RunAggregate,
    events: list[StoredEvent],
    candidate: CommandEnvelope,
) -> RunState:
    state = replay(aggregate, events, stream_id=str(RUN_ID)).state
    decision = aggregate.decide(state, candidate)
    materialize(decision.events, events)
    return replay(aggregate, events, stream_id=str(RUN_ID)).state


@pytest.mark.parametrize("family", list(RunFamily))
def test_every_family_uses_the_same_create_start_complete_lifecycle(
    family: RunFamily,
) -> None:
    aggregate = RunAggregate()
    events: list[StoredEvent] = []

    created = decide_and_replay(
        aggregate,
        events,
        command(
            "CreateRun",
            0,
            {
                "family": family.value,
                "source_entity_type": "session",
                "source_entity_id": "session-1",
                "parent_run_id": str(PARENT_ID) if family == RunFamily.REMEDIATION else None,
                "semantic_payload": {"goal": "test"},
            },
        ),
    )
    running = decide_and_replay(aggregate, events, command("StartRun", 1))
    completed = decide_and_replay(
        aggregate,
        events,
        command("CompleteRun", 2, {"result_ref": "object://result"}),
    )

    assert created.family == family
    assert created.parent_run_id == (PARENT_ID if family == RunFamily.REMEDIATION else None)
    assert created.correlation_id == CORRELATION_ID
    assert created.policy_snapshot is not None
    assert created.policy_snapshot.family is family
    assert running.status == "running"
    assert completed.status == "completed"
    assert completed.terminal_event_id == events[-1].event_id
    assert "semantic_payload" not in events[0].public_payload
    assert events[0].internal_payload["semantic_payload"] == {"goal": "test"}
    assert events[0].internal_payload["policy_snapshot"]["family"] == family.value
    assert "policy_snapshot" not in events[0].public_payload
    assert [event.event_type for event in events] == [
        "RunCreated",
        "RunStarted",
        "RunCompleted",
    ]


def test_cancel_complete_race_has_exactly_one_terminal_event() -> None:
    aggregate = RunAggregate()
    events: list[StoredEvent] = []
    decide_and_replay(
        aggregate,
        events,
        command(
            "CreateRun",
            0,
            {
                "family": "agent",
                "source_entity_type": "session",
                "source_entity_id": "session-1",
                "parent_run_id": None,
                "semantic_payload": {},
            },
        ),
    )
    decide_and_replay(aggregate, events, command("StartRun", 1))
    terminal = decide_and_replay(aggregate, events, command("CancelRun", 2))

    with pytest.raises(InvalidRunTransitionError):
        aggregate.decide(terminal, command("CompleteRun", 3))

    assert (
        sum(event.event_type in {"RunCompleted", "RunFailed", "RunCancelled"} for event in events)
        == 1
    )


def test_cancel_run_formally_settles_every_active_activity() -> None:
    aggregate = RunAggregate()
    activity_id = UUID("20000000-0000-0000-0000-000000000077")
    events: list[StoredEvent] = []
    decide_and_replay(
        aggregate,
        events,
        command(
            "CreateRun",
            0,
            {
                "family": "agent",
                "source_entity_type": "session",
                "source_entity_id": "session-1",
                "parent_run_id": None,
                "semantic_payload": {},
            },
        ),
    )
    decide_and_replay(aggregate, events, command("StartRun", 1))
    decide_and_replay(
        aggregate,
        events,
        command(
            "RequestActivity",
            2,
            {
                "activity_id": str(activity_id),
                "activity_type": "model.call",
                "timeout_at": (NOW + timedelta(minutes=5)).isoformat(),
                "input_ref": "object://request",
                "input_digest": "a" * 64,
            },
        ),
    )
    decide_and_replay(
        aggregate,
        events,
        command(
            "MarkActivityCallStarted",
            3,
            {
                "activity_id": str(activity_id),
                "generation": 0,
                "result_ref": None,
                "result_summary": None,
            },
        ),
    )

    cancelled = decide_and_replay(
        aggregate,
        events,
        command("CancelRun", 4),
    )

    assert [event.event_type for event in events[-2:]] == [
        "ActivityCancelled",
        "RunCancelled",
    ]
    assert cancelled.status == "cancelled"
    assert cancelled.active_activity_ids == ()
    assert cancelled.settled_activities == ((activity_id, "cancelled", 0),)
    assert cancelled.activity_failure_codes == ((activity_id, 0, "ACTIVITY_CANCELLED"),)


def test_retryable_failure_waits_then_increments_generation() -> None:
    aggregate = RunAggregate()
    events: list[StoredEvent] = []
    decide_and_replay(
        aggregate,
        events,
        command(
            "CreateRun",
            0,
            {
                "family": "ask",
                "source_entity_type": "session",
                "source_entity_id": "session-1",
                "parent_run_id": None,
                "semantic_payload": {},
            },
        ),
    )
    decide_and_replay(aggregate, events, command("StartRun", 1))
    waiting = decide_and_replay(
        aggregate,
        events,
        command(
            "FailRun",
            2,
            {"failure_code": "PROVIDER_TIMEOUT", "retryable": True},
        ),
    )
    retried = decide_and_replay(aggregate, events, command("RetryRun", 3))

    assert waiting.status == "waiting"
    assert waiting.wait_reason == "retry"
    assert retried.status == "queued"
    assert retried.retry_generation == 1
    assert retried.terminal_event_id is None
    assert events[-2].event_type == "RunAttemptFailed"
    assert events[-1].event_type == "RunRetried"


def test_retryable_failure_schedules_deterministic_backoff_retry_timer() -> None:
    """K2-4: retries are timer-driven with exponential backoff, not immediate."""
    from uuid import NAMESPACE_URL, uuid5

    aggregate = RunAggregate()
    events: list[StoredEvent] = []
    decide_and_replay(
        aggregate,
        events,
        command(
            "CreateRun",
            0,
            {
                "family": "ask",
                "source_entity_type": "session",
                "source_entity_id": "session-1",
                "parent_run_id": None,
                "semantic_payload": {},
            },
        ),
    )
    decide_and_replay(aggregate, events, command("StartRun", 1))
    state = replay(aggregate, events, stream_id=str(RUN_ID)).state

    decision = aggregate.decide(
        state,
        command("FailRun", 2, {"failure_code": "PROVIDER_TIMEOUT", "retryable": True}),
    )

    assert [event.event_type for event in decision.events] == ["RunAttemptFailed"]
    assert len(decision.scheduled_commands) == 1
    timer = decision.scheduled_commands[0]
    expected_timer_id = uuid5(NAMESPACE_URL, f"opencitadel:run-retry:{RUN_ID}:0")
    assert timer.timer_id == expected_timer_id
    # Generation 0: base backoff of 5s from the command's issued_at.
    assert timer.due_at == NOW + timedelta(seconds=5)
    assert timer.command.command_type == "RetryRun"
    assert timer.command.command_id == expected_timer_id
    assert timer.command.expected_stream_version is None
    assert timer.cancellation_event_types == frozenset(
        {"RunRetried", "RunCancelled", "RunCompleted", "RunFailed"}
    )

    # Higher retry generations back off exponentially and are capped at 300s.
    generation_3 = state.model_copy(update={"retry_generation": 3})
    backoff_3 = aggregate.decide(
        generation_3,
        command("FailRun", 2, {"failure_code": "PROVIDER_TIMEOUT", "retryable": True}),
    ).scheduled_commands[0]
    assert backoff_3.due_at == NOW + timedelta(seconds=40)  # 5 * 2^3
    generation_9 = state.model_copy(update={"retry_generation": 9})
    capped = aggregate.decide(
        generation_9,
        command("FailRun", 2, {"failure_code": "PROVIDER_TIMEOUT", "retryable": True}),
    ).scheduled_commands[0]
    assert capped.due_at == NOW + timedelta(seconds=300)  # min(5 * 2^9, 300)

    # A terminal (non-retryable) failure schedules nothing.
    terminal = aggregate.decide(
        state,
        command("FailRun", 2, {"failure_code": "PROVIDER_DOWN", "retryable": False}),
    )
    assert terminal.scheduled_commands == ()
    assert [event.event_type for event in terminal.events] == ["RunFailed"]


@pytest.mark.parametrize(
    ("decision", "terminal_type", "expected_status"),
    [
        ("approved", None, "running"),
        ("rejected", "RunCancelled", "cancelled"),
    ],
)
def test_approval_is_an_explicit_run_command_not_a_chat_message(
    decision: str,
    terminal_type: str | None,
    expected_status: str,
) -> None:
    aggregate = RunAggregate()
    approval_id = UUID("30000000-0000-0000-0000-000000000001")
    activity_id = UUID("30000000-0000-0000-0000-000000000002")
    events: list[StoredEvent] = []
    decide_and_replay(
        aggregate,
        events,
        command(
            "CreateRun",
            0,
            {
                "family": "agent",
                "source_entity_type": "session",
                "source_entity_id": "session-1",
                "parent_run_id": None,
                "semantic_payload": {},
            },
        ),
    )
    decide_and_replay(aggregate, events, command("StartRun", 1))
    waiting = decide_and_replay(
        aggregate,
        events,
        command(
            "RequestApproval",
            2,
            {
                "approval_id": str(approval_id),
                "subject_activity_id": str(activity_id),
                "approval_kind": "tool_effect",
                "risk_summary": "Write to an external system",
                "subject_label": "write_external",
            },
        ),
    )

    assert waiting.status == "waiting"
    assert waiting.wait_reason == "approval"
    assert waiting.pending_approval_id == approval_id
    decided = decide_and_replay(
        aggregate,
        events,
        command(
            "DecideApproval",
            3,
            {
                "approval_id": str(approval_id),
                "decision": decision,
                "actor_user_id": "reviewer-1",
                "feedback": "reviewed",
            },
        ),
    )

    assert decided.status == expected_status
    assert decided.pending_approval_id is None
    assert next((dec, fb) for aid, dec, fb in decided.approval_decisions if aid == approval_id) == (
        decision,
        "reviewed",
    )
    assert events[-2].event_type == "ApprovalDecided"
    assert events[-1].event_type == (terminal_type or "RunResumed")


def test_activity_request_is_durable_and_active_identity_is_replayed() -> None:
    aggregate = RunAggregate()
    activity_id = UUID("20000000-0000-0000-0000-000000000001")
    events: list[StoredEvent] = []
    decide_and_replay(
        aggregate,
        events,
        command(
            "CreateRun",
            0,
            {
                "family": "agent",
                "source_entity_type": "session",
                "source_entity_id": "session-1",
                "parent_run_id": None,
                "semantic_payload": {},
            },
        ),
    )
    decide_and_replay(aggregate, events, command("StartRun", 1))
    state = replay(aggregate, events, stream_id=str(RUN_ID)).state

    decision = aggregate.decide(
        state,
        command(
            "RequestActivity",
            2,
            {
                "activity_id": str(activity_id),
                "activity_type": "model.call",
                "timeout_at": (NOW + timedelta(minutes=5)).isoformat(),
                "input_ref": "object://request",
                "input_digest": "a" * 64,
            },
        ),
    )
    materialize(decision.events, events)
    projected = replay(aggregate, events, stream_id=str(RUN_ID)).state

    assert decision.activity_requests[0].activity_id == activity_id
    assert len(decision.scheduled_commands) == 1
    timeout = decision.scheduled_commands[0]
    assert timeout.due_at == NOW + timedelta(minutes=5)
    assert timeout.command.command_type == "FailActivity"
    assert timeout.command.payload == {
        "activity_id": str(activity_id),
        "generation": 0,
        "failure_code": "ACTIVITY_TIMEOUT",
    }
    assert timeout.cancellation_activity_id == activity_id
    assert projected.active_activity_ids == (activity_id,)
    assert events[-1].event_type == "ActivityRequested"

    started = aggregate.decide(
        projected,
        command(
            "MarkActivityCallStarted",
            3,
            {
                "activity_id": str(activity_id),
                "generation": 0,
                "result_ref": None,
                "result_summary": None,
            },
        ),
    )
    materialize(started.events, events)
    after_start = replay(aggregate, events, stream_id=str(RUN_ID)).state
    assert (
        aggregate.decide(
            after_start,
            command(
                "MarkActivityCallStarted",
                4,
                {
                    "activity_id": str(activity_id),
                    "generation": 0,
                    "result_ref": None,
                    "result_summary": None,
                },
            ),
        ).events
        == ()
    )

    completed = aggregate.decide(
        after_start,
        command(
            "CompleteActivity",
            4,
            {
                "activity_id": str(activity_id),
                "generation": 0,
                "result_ref": "object://result",
                "result_summary": "ok",
            },
        ),
    )
    materialize(completed.events, events)
    settled = replay(aggregate, events, stream_id=str(RUN_ID)).state
    assert (
        aggregate.decide(
            settled,
            command(
                "CompleteActivity",
                5,
                {
                    "activity_id": str(activity_id),
                    "generation": 0,
                    "result_ref": "object://result",
                    "result_summary": "ok",
                },
            ),
        ).events
        == ()
    )


@pytest.mark.parametrize(
    ("command_type", "expected_status"),
    [
        ("FailActivity", "failed"),
        ("MarkActivityOutcomeUnknown", "unknown"),
    ],
)
def test_activity_failure_code_is_preserved_by_replay(
    command_type: str,
    expected_status: str,
) -> None:
    aggregate = RunAggregate()
    activity_id = UUID("20000000-0000-0000-0000-000000000002")
    events: list[StoredEvent] = []
    decide_and_replay(
        aggregate,
        events,
        command(
            "CreateRun",
            0,
            {
                "family": "kb_ingest",
                "source_entity_type": "resource_build",
                "source_entity_id": "build-1",
                "parent_run_id": None,
                "semantic_payload": {},
            },
        ),
    )
    decide_and_replay(aggregate, events, command("StartRun", 1))
    decide_and_replay(
        aggregate,
        events,
        command(
            "RequestActivity",
            2,
            {
                "activity_id": str(activity_id),
                "activity_type": "knowledge.build",
                "timeout_at": (NOW + timedelta(minutes=5)).isoformat(),
                "input_ref": "object://request",
                "input_digest": "a" * 64,
            },
        ),
    )

    settled = decide_and_replay(
        aggregate,
        events,
        command(
            command_type,
            3,
            {
                "activity_id": str(activity_id),
                "generation": 0,
                "failure_code": "KNOWLEDGE_NO_INDEXABLE_SOURCE",
            },
        ),
    )

    assert settled.settled_activities == ((activity_id, expected_status, 0),)
    assert settled.activity_failure_codes == ((activity_id, 0, "KNOWLEDGE_NO_INDEXABLE_SOURCE"),)


def test_activity_progress_is_not_an_aggregate_concern() -> None:
    """Progress is off-stream telemetry; the aggregate refuses it outright."""
    aggregate = RunAggregate()

    with pytest.raises(UnknownRunCommandError):
        aggregate.decide(
            aggregate.initial_state(str(RUN_ID)),
            command("ReportActivityProgress", 0, {"activity_id": str(UUID(int=9))}),
        )
    assert "ReportActivityProgress" not in aggregate.command_registry.registered_names()
    assert "ActivityProgressed" not in aggregate.event_registry.registered_names()


def test_complete_activity_keeps_decision_payload_off_stream_as_a_digest() -> None:
    aggregate = RunAggregate()
    activity_id = UUID("20000000-0000-0000-0000-000000000077")
    decision_data = {"tool_calls": [{"call_id": "c1", "name": "shell_exec"}]}
    events: list[StoredEvent] = []
    decide_and_replay(
        aggregate,
        events,
        command(
            "CreateRun",
            0,
            {
                "family": "agent",
                "source_entity_type": "session",
                "source_entity_id": "session-1",
                "parent_run_id": None,
                "semantic_payload": {},
            },
        ),
    )
    decide_and_replay(aggregate, events, command("StartRun", 1))
    decide_and_replay(
        aggregate,
        events,
        command(
            "RequestActivity",
            2,
            {
                "activity_id": str(activity_id),
                "activity_type": "model.call",
                "timeout_at": (NOW + timedelta(minutes=5)).isoformat(),
                "input_ref": "object://request",
                "input_digest": "a" * 64,
            },
        ),
    )
    state = decide_and_replay(
        aggregate,
        events,
        command(
            "CompleteActivity",
            3,
            {
                "activity_id": str(activity_id),
                "generation": 0,
                "result_ref": "object://result",
                "result_summary": "ok",
                "decision_data": decision_data,
            },
        ),
    )

    completed = events[-1]
    assert completed.event_type == "ActivityCompleted"
    assert "decision_data" not in completed.public_payload
    assert completed.internal_payload == {"decision_digest": decision_data_digest(decision_data)}
    assert state.activity_results == (
        (activity_id, 0, "object://result", "ok", decision_data_digest(decision_data)),
    )


def test_activity_results_preserve_causal_completion_order() -> None:
    aggregate = RunAggregate()
    first_id = UUID("f0000000-0000-0000-0000-000000000001")
    second_id = UUID("10000000-0000-0000-0000-000000000002")
    events: list[StoredEvent] = []
    decide_and_replay(
        aggregate,
        events,
        command(
            "CreateRun",
            0,
            {
                "family": "agent",
                "source_entity_type": "session",
                "source_entity_id": "session-1",
                "parent_run_id": None,
                "semantic_payload": {},
            },
        ),
    )
    decide_and_replay(aggregate, events, command("StartRun", 1))

    for activity_id, version, result_ref in (
        (first_id, 2, "object://first"),
        (second_id, 4, "object://second"),
    ):
        decide_and_replay(
            aggregate,
            events,
            command(
                "RequestActivity",
                version,
                {
                    "activity_id": str(activity_id),
                    "activity_type": "model.call",
                    "timeout_at": (NOW + timedelta(minutes=5)).isoformat(),
                    "input_ref": "object://request",
                    "input_digest": "a" * 64,
                },
            ),
        )
        state = decide_and_replay(
            aggregate,
            events,
            command(
                "CompleteActivity",
                version + 1,
                {
                    "activity_id": str(activity_id),
                    "generation": 0,
                    "result_ref": result_ref,
                    "result_summary": "ok",
                },
            ),
        )

    assert result_refs(state) == ["object://first", "object://second"]


def test_full_replay_equals_every_snapshot_tail_split() -> None:
    aggregate = RunAggregate()
    events: list[StoredEvent] = []
    decide_and_replay(
        aggregate,
        events,
        command(
            "CreateRun",
            0,
            {
                "family": "patrol",
                "source_entity_type": "patrol_pack",
                "source_entity_id": "pack-1",
                "parent_run_id": None,
                "semantic_payload": {},
            },
        ),
    )
    decide_and_replay(aggregate, events, command("StartRun", 1))
    decide_and_replay(
        aggregate,
        events,
        command("WaitRun", 2, {"reason": "approval"}),
    )
    decide_and_replay(aggregate, events, command("ResumeRun", 3))
    decide_and_replay(
        aggregate,
        events,
        command("CompleteRun", 4, {"result_ref": None}),
    )
    full = replay(aggregate, events, stream_id=str(RUN_ID))

    for split in range(len(events) + 1):
        prefix = replay(aggregate, events[:split], stream_id=str(RUN_ID))
        snapshot = ReplaySnapshot(
            stream_id=str(RUN_ID),
            stream_version=prefix.stream_version,
            state=prefix.state,
            state_hash=prefix.state_hash,
            last_event_hash=prefix.last_event_hash,
        )
        resumed = replay(
            aggregate,
            events[split:],
            snapshot=snapshot,
            stream_id=str(RUN_ID),
        )
        assert resumed.state == full.state
        assert resumed.state_hash == full.state_hash


def _approve_pending(aggregate: RunAggregate, events: list[StoredEvent]) -> None:
    decide_and_replay(
        aggregate,
        events,
        command(
            "CreateRun",
            0,
            {
                "family": "agent",
                "source_entity_type": "session",
                "source_entity_id": "session-1",
                "parent_run_id": None,
                "semantic_payload": {},
            },
        ),
    )
    decide_and_replay(aggregate, events, command("StartRun", 1))


APPROVAL_ID = UUID("40000000-0000-0000-0000-000000000001")
APPROVAL_ACTIVITY_ID = UUID("40000000-0000-0000-0000-000000000002")
REQUEST_APPROVAL_PAYLOAD = {
    "approval_id": str(APPROVAL_ID),
    "subject_activity_id": str(APPROVAL_ACTIVITY_ID),
    "approval_kind": "tool_effect",
    "risk_summary": "Write to an external system",
    "subject_label": "write_external",
}


def test_request_approval_schedules_a_self_cancelling_timeout() -> None:
    aggregate = RunAggregate()
    events: list[StoredEvent] = []
    _approve_pending(aggregate, events)
    state = replay(aggregate, events, stream_id=str(RUN_ID)).state

    decision = aggregate.decide(
        state,
        command("RequestApproval", 2, REQUEST_APPROVAL_PAYLOAD),
    )

    assert [event.event_type for event in decision.events] == ["ApprovalRequested"]
    assert len(decision.scheduled_commands) == 1
    timeout = decision.scheduled_commands[0]
    assert timeout.command.command_type == "ExpireApproval"
    assert timeout.command.payload == {"approval_id": str(APPROVAL_ID)}
    assert timeout.due_at == NOW + timedelta(minutes=1440)
    assert "ApprovalDecided" in timeout.cancellation_event_types
    assert "RunCancelled" in timeout.cancellation_event_types
    assert timeout.cancellation_activity_id is None


def test_expired_approval_advances_a_permanently_waiting_run() -> None:
    aggregate = RunAggregate()
    events: list[StoredEvent] = []
    _approve_pending(aggregate, events)
    waiting = decide_and_replay(
        aggregate,
        events,
        command("RequestApproval", 2, REQUEST_APPROVAL_PAYLOAD),
    )
    assert waiting.status == "waiting"
    assert waiting.pending_approval_id == APPROVAL_ID

    expired = decide_and_replay(
        aggregate,
        events,
        command("ExpireApproval", 3, {"approval_id": str(APPROVAL_ID)}),
    )

    assert [event.event_type for event in events[-2:]] == [
        "ApprovalExpired",
        "RunCancelled",
    ]
    assert expired.status == "cancelled"
    assert expired.pending_approval_id is None
    assert (
        next(dec for aid, dec, _ in expired.approval_decisions if aid == APPROVAL_ID) == "expired"
    )


def test_expire_is_a_noop_once_the_approval_was_already_decided() -> None:
    aggregate = RunAggregate()
    events: list[StoredEvent] = []
    _approve_pending(aggregate, events)
    decide_and_replay(
        aggregate,
        events,
        command("RequestApproval", 2, REQUEST_APPROVAL_PAYLOAD),
    )
    resumed = decide_and_replay(
        aggregate,
        events,
        command(
            "DecideApproval",
            3,
            {
                "approval_id": str(APPROVAL_ID),
                "decision": "approved",
                "actor_user_id": "reviewer-1",
                "feedback": "ok",
            },
        ),
    )
    assert resumed.status == "running"

    # A late timer for the already-decided approval must not emit any event.
    late = aggregate.decide(
        resumed,
        command("ExpireApproval", resumed.stream_version, {"approval_id": str(APPROVAL_ID)}),
    )
    assert late.events == ()


def test_expire_after_run_is_terminal_is_absorbed() -> None:
    aggregate = RunAggregate()
    events: list[StoredEvent] = []
    _approve_pending(aggregate, events)
    terminal = decide_and_replay(aggregate, events, command("CancelRun", 2))
    assert terminal.status == "cancelled"

    absorbed = aggregate.decide(
        terminal,
        command("ExpireApproval", terminal.stream_version, {"approval_id": str(APPROVAL_ID)}),
    )
    assert absorbed.events == ()


def test_request_approval_honours_policy_injected_ttl() -> None:
    """The operations-policy TTL carried on the command must drive the timeout."""
    aggregate = RunAggregate()
    events: list[StoredEvent] = []
    _approve_pending(aggregate, events)
    state = replay(aggregate, events, stream_id=str(RUN_ID)).state

    decision = aggregate.decide(
        state,
        command(
            "RequestApproval",
            2,
            {**REQUEST_APPROVAL_PAYLOAD, "ttl_minutes": 30},
        ),
    )

    assert decision.scheduled_commands[0].due_at == NOW + timedelta(minutes=30)
