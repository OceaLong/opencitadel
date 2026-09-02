"""Agent workflow transition tests for governed multi-turn conversations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.decisions import DecisionFacts
from app.kernel.domain.state import RunState, apply_event
from app.kernel.domain.types import EffectSafety, OwnerScopeRef, RunStatus, Workflow
from app.kernel.domain.workflows.agent import AgentWorkflowError, agent_reducer

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
RUN_ID = UUID("00000000-0000-0000-0000-000000000101")
MODEL_EFFECT_ID = UUID("00000000-0000-0000-0000-000000000501")
TOOL_EFFECT_ID = UUID("00000000-0000-0000-0000-000000000502")


def _command(type_: str, payload: dict[str, object], version: int | None = None):
    return CommandEnvelope(
        command_id=UUID(int=800 + (version or 0)),
        run_id=RUN_ID,
        workflow=Workflow.AGENT,
        type=type_,
        payload=payload,
        expected_stream_version=version,
        owner_scope=OwnerScopeRef.personal("user-1"),
        actor_user_id="user-1",
        request_id="request-1",
        submitted_at=NOW,
    )


def _facts(*, event_count: int = 8, effect_ids=(), approval_ids=(), timer_ids=()):
    return DecisionFacts(
        now=NOW,
        actor_user_id="user-1",
        request_id="request-1",
        policy_revision_id=UUID(int=700),
        event_ids=tuple(UUID(int=1_000 + index) for index in range(event_count)),
        effect_ids=tuple(effect_ids),
        timer_ids=tuple(timer_ids),
        approval_ids=tuple(approval_ids),
        reviewer_user_ids=("team-owner", "team-admin"),
    )


def _idle_state() -> RunState:
    return RunState(
        run_id=RUN_ID,
        workflow=Workflow.AGENT,
        owner_scope=OwnerScopeRef.personal("user-1"),
        status=RunStatus.IDLE,
        stream_version=4,
        stream_hash="a" * 64,
        current_turn=1,
        data={
            "tool_catalog": [
                {
                    "name": "shell.run",
                    "safety": "non_idempotent_write",
                    "requires_approval": True,
                }
            ],
            "knowledge_version_ids": ["kb-version-1"],
        },
    )


def test_start_agent_retrieves_pinned_knowledge_before_requesting_a_model() -> None:
    """Pinned knowledge is an explicit Effect and cannot be silently skipped."""

    decision = agent_reducer(
        None,
        _command(
            "StartAgent",
            {
                "title": "Repository audit",
                "prompt": "inspect the repository",
                "tool_catalog": [],
                "knowledge_version_ids": ["kb-version-1"],
            },
            version=0,
        ),
        _facts(effect_ids=(MODEL_EFFECT_ID,)),
    )

    assert tuple(event.type for event in decision.events) == (
        "RunStarted",
        "PromptAccepted",
        "EffectRequested",
    )
    assert len(decision.effects) == 1
    assert decision.effects[0].effect_id == MODEL_EFFECT_ID
    assert decision.effects[0].type == "knowledge.retrieve"
    assert decision.effects[0].safety is EffectSafety.READ_ONLY
    assert decision.effects[0].request["knowledge_version_ids"] == ["kb-version-1"]
    assert decision.effects[0].request["continuation"]["prompt"] == "inspect the repository"


def test_idle_agent_accepts_another_prompt_but_running_agent_does_not() -> None:
    """Two overlapping turns must not compete inside one conversation stream."""

    idle = _idle_state()
    accepted = agent_reducer(
        idle,
        _command("SubmitPrompt", {"prompt": "continue"}, version=4),
        _facts(effect_ids=(MODEL_EFFECT_ID,)),
    )
    running = idle.model_copy(update={"status": RunStatus.RUNNING})

    assert tuple(event.type for event in accepted.events) == (
        "PromptAccepted",
        "EffectRequested",
    )
    assert accepted.effects[0].type == "knowledge.retrieve"
    with pytest.raises(AgentWorkflowError, match="idle"):
        agent_reducer(
            running,
            _command("SubmitPrompt", {"prompt": "overlap"}, version=4),
            _facts(effect_ids=(TOOL_EFFECT_ID,)),
        )


def test_retrieval_result_is_bound_into_the_following_model_effect() -> None:
    state = _idle_state().model_copy(
        update={"status": RunStatus.RUNNING, "active_effect_ids": (MODEL_EFFECT_ID,)}
    )

    decision = agent_reducer(
        state,
        _command(
            "EffectSucceeded",
            {
                "effect_id": str(MODEL_EFFECT_ID),
                "effect_type": "knowledge.retrieve",
                "prompt": "continue",
                "matches": [{"text": "evidence", "score": 1.0}],
            },
            version=4,
        ),
        _facts(effect_ids=(TOOL_EFFECT_ID,)),
    )

    assert tuple(event.type for event in decision.events) == (
        "EffectSucceeded",
        "EffectRequested",
    )
    assert decision.effects[0].type == "model.call"
    assert decision.effects[0].request["retrieval_matches"] == [{"text": "evidence", "score": 1.0}]


def test_model_cannot_expand_the_frozen_tool_catalog() -> None:
    """Provider text must never invent a callable capability."""

    state = _idle_state().model_copy(
        update={"status": RunStatus.RUNNING, "active_effect_ids": (MODEL_EFFECT_ID,)}
    )

    with pytest.raises(AgentWorkflowError, match="not in the frozen catalog"):
        agent_reducer(
            state,
            _command(
                "EffectSucceeded",
                {
                    "effect_id": str(MODEL_EFFECT_ID),
                    "effect_type": "model.call",
                    "tool_calls": [{"name": "network.exfiltrate", "arguments": {}}],
                },
                version=4,
            ),
            _facts(effect_ids=(TOOL_EFFECT_ID,)),
        )


def test_governed_tool_is_blocked_on_a_frozen_approval() -> None:
    """A governed tool must not become claimable before approval."""

    state = _idle_state().model_copy(
        update={"status": RunStatus.RUNNING, "active_effect_ids": (MODEL_EFFECT_ID,)}
    )
    approval_id = UUID(int=900)
    timer_id = UUID(int=901)

    decision = agent_reducer(
        state,
        _command(
            "EffectSucceeded",
            {
                "effect_id": str(MODEL_EFFECT_ID),
                "effect_type": "model.call",
                "tool_calls": [{"name": "shell.run", "arguments": {"command": "pwd"}}],
            },
            version=4,
        ),
        _facts(
            effect_ids=(TOOL_EFFECT_ID,),
            approval_ids=(approval_id,),
            timer_ids=(timer_id,),
        ),
    )

    assert tuple(event.type for event in decision.events) == (
        "EffectSucceeded",
        "ApprovalRequested",
        "EffectRequested",
        "RunWaiting",
    )
    tool = decision.effects[0]
    assert tool.effect_id == TOOL_EFFECT_ID
    assert tool.requires_approval is True
    assert tool.approval_id == approval_id
    assert tool.reviewer_user_ids == ("team-owner", "team-admin")
    assert len(decision.timers) == 1
    assert decision.timers[0].timer_id == timer_id
    assert decision.timers[0].due_at == NOW + timedelta(hours=24)
    assert decision.events[1].public_payload["reviewer_user_ids"] == [
        "team-owner",
        "team-admin",
    ]


def test_frozen_reviewer_can_release_the_only_pending_effect() -> None:
    """Approval must release the original Effect without minting a new invocation."""

    approval_id = UUID(int=902)
    state = _idle_state().model_copy(
        update={
            "status": RunStatus.WAITING,
            "active_effect_ids": (TOOL_EFFECT_ID,),
            "pending_approvals": (
                {
                    "approval_id": approval_id,
                    "effect_id": TOOL_EFFECT_ID,
                    "timer_id": UUID(int=909),
                    "reviewer_user_ids": ("team-owner", "team-admin"),
                    "expires_at": NOW + timedelta(hours=24),
                },
            ),
        }
    )
    command = _command(
        "DecideApproval",
        {"approval_id": str(approval_id), "decision": "approved"},
        version=4,
    ).model_copy(update={"actor_user_id": "team-admin"})

    decision = agent_reducer(state, command, _facts())

    assert tuple(event.type for event in decision.events) == (
        "ApprovalDecided",
        "EffectReleased",
        "RunResumed",
    )
    assert decision.events[1].public_payload["effect_id"] == str(TOOL_EFFECT_ID)


def test_non_reviewer_cannot_decide_and_expiry_cancels_the_effect() -> None:
    """The frozen reviewer set and durable expiry are reducer invariants."""

    approval_id = UUID(int=903)
    state = _idle_state().model_copy(
        update={
            "status": RunStatus.WAITING,
            "active_effect_ids": (TOOL_EFFECT_ID,),
            "pending_approvals": (
                {
                    "approval_id": approval_id,
                    "effect_id": TOOL_EFFECT_ID,
                    "timer_id": UUID(int=910),
                    "reviewer_user_ids": ("team-owner", "team-admin"),
                    "expires_at": NOW,
                },
            ),
        }
    )
    outsider = _command(
        "DecideApproval",
        {"approval_id": str(approval_id), "decision": "approved"},
        version=4,
    ).model_copy(update={"actor_user_id": "outsider"})

    with pytest.raises(AgentWorkflowError, match="reviewer"):
        agent_reducer(state, outsider, _facts())

    expired = agent_reducer(
        state,
        _command("ExpireApproval", {"approval_id": str(approval_id)}, version=4),
        _facts(),
    )
    assert tuple(event.type for event in expired.events) == (
        "ApprovalExpired",
        "EffectCancelled",
        "TurnCompleted",
    )


def test_model_multi_tool_fanout_is_rejected_to_keep_one_governed_effect_per_turn() -> None:
    """The greenfield core deliberately removes ambiguous multi-approval fanout."""

    state = _idle_state().model_copy(
        update={"status": RunStatus.RUNNING, "active_effect_ids": (MODEL_EFFECT_ID,)}
    )
    with pytest.raises(AgentWorkflowError, match="one tool call"):
        agent_reducer(
            state,
            _command(
                "EffectSucceeded",
                {
                    "effect_id": str(MODEL_EFFECT_ID),
                    "effect_type": "model.call",
                    "tool_calls": [
                        {"name": "shell.run", "arguments": {"command": "pwd"}},
                        {"name": "shell.run", "arguments": {"command": "ls"}},
                    ],
                },
                version=4,
            ),
            _facts(
                effect_ids=(TOOL_EFFECT_ID, UUID(int=904)),
                approval_ids=(UUID(int=905), UUID(int=906)),
                timer_ids=(UUID(int=907), UUID(int=908)),
            ),
        )


def test_final_model_message_returns_the_conversation_to_idle() -> None:
    """A turn without tool calls must close with a replayable idle transition."""

    state = _idle_state().model_copy(
        update={"status": RunStatus.RUNNING, "active_effect_ids": (MODEL_EFFECT_ID,)}
    )
    decision = agent_reducer(
        state,
        _command(
            "EffectSucceeded",
            {
                "effect_id": str(MODEL_EFFECT_ID),
                "effect_type": "model.call",
                "content": "The repository is healthy.",
                "tool_calls": [],
            },
            version=4,
        ),
        _facts(),
    )

    assert tuple(event.type for event in decision.events) == (
        "EffectSucceeded",
        "AssistantMessageCreated",
        "TurnCompleted",
    )


def test_cancellation_is_terminal_and_cancels_active_effects() -> None:
    """Cancellation must prevent a pending external call from advancing later."""

    state = _idle_state().model_copy(
        update={"status": RunStatus.WAITING, "active_effect_ids": (TOOL_EFFECT_ID,)}
    )
    decision = agent_reducer(
        state,
        _command("CancelRun", {"reason": "user_requested"}, version=4),
        _facts(),
    )

    assert tuple(event.type for event in decision.events) == (
        "EffectCancelled",
        "ApprovalCancelled",
        "RunCancelled",
    )
    replayed = state
    for version, event in enumerate(decision.events, start=5):
        stored = type("Event", (), {})()
        stored.run_id = RUN_ID
        stored.owner_scope = state.owner_scope
        stored.type = event.type
        stored.public_payload = event.public_payload
        stored.version = version
        stored.hash = f"{version:064x}"
        stored.previous_hash = replayed.stream_hash
        replayed = apply_event(replayed, stored)
    assert replayed.status is RunStatus.CANCELLED
    assert replayed.active_effect_ids == ()
