"""Pure workflow decisions for every production Run family."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.execution.decisions import next_command
from app.application.execution.run_context import run_execution_context
from app.domain.execution.run import RunFamily, RunState, RunStatus
from app.domain.runtime_policy import AgentExecutionPolicy, ExecutionPolicy
from tests.app.execution_test_support import run_policy_snapshot_json

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)
RUN_ID = UUID("50000000-0000-0000-0000-000000000001")


def _state(
    family: RunFamily,
    *,
    policy: ExecutionPolicy | None = None,
    **updates: object,
) -> RunState:
    base = RunState(
        run_id=RUN_ID,
        family=family,
        source_entity_type="session",
        source_entity_id="session-1",
        semantic_payload={
            "input_ref": "object://input",
            "input_digest": "a" * 64,
        },
        policy_snapshot=run_policy_snapshot_json(family, policy=policy),
        status=RunStatus.RUNNING,
        stream_version=2,
        owner_user_id="user-1",
        correlation_id=UUID(int=9),
    )
    return base.model_copy(update=updates)


def _next(state: RunState):
    return next_command(state, run_execution_context(state), now=NOW)


def _after_retrieval(state: RunState) -> RunState:
    request = _next(state)
    assert request is not None
    assert request.payload["activity_type"] == "retrieval.search"
    activity_id = UUID(str(request.payload["activity_id"]))
    return state.model_copy(
        update={
            "stream_version": state.stream_version + 3,
            "settled_activities": ((activity_id, "succeeded", 0),),
            "activity_results": ((activity_id, 0, "result://retrieval", "found", {}),),
        }
    )


def test_queued_run_starts_before_family_decision() -> None:
    state = _state(RunFamily.AGENT, status=RunStatus.QUEUED, stream_version=1)

    command = _next(state)

    assert command is not None
    assert command.command_type == "StartRun"


def test_agent_retrieves_snapshot_bound_context_before_tool_enabled_model() -> None:
    running = _state(RunFamily.AGENT)
    retrieval = _next(running)

    assert retrieval is not None
    assert retrieval.payload["activity_type"] == "retrieval.search"

    command = _next(_after_retrieval(running))

    assert command is not None
    assert command.command_type == "RequestActivity"
    assert command.payload["activity_type"] == "model.call"
    assert command.payload["input_payload"] == {
        "allow_tools": True,
        "history_refs": ["result://retrieval"],
        "round": 0,
    }


def test_agent_routes_model_tool_intent_through_durable_approval() -> None:
    running = _after_retrieval(_state(RunFamily.AGENT))
    model_request = _next(running)
    assert model_request is not None
    model_id = UUID(str(model_request.payload["activity_id"]))
    after_model = running.model_copy(
        update={
            "stream_version": 5,
            "settled_activities": (
                *running.settled_activities,
                (model_id, "succeeded", 0),
            ),
            "activity_results": (
                *running.activity_results,
                (
                    model_id,
                    0,
                    "result://model-0",
                    "",
                    {
                        "tool_calls": [
                            {
                                "call_id": "call-1",
                                "name": "write_file",
                                "arguments": {
                                    "filepath": "/work/a",
                                    "content": "x",
                                },
                                "requires_approval": True,
                                "risk_summary": "Write workspace file",
                            }
                        ]
                    },
                ),
            ),
        }
    )

    approval = _next(after_model)

    assert approval is not None
    assert approval.command_type == "RequestApproval"
    approval_id = UUID(str(approval.payload["approval_id"]))
    approved = after_model.model_copy(update={"approval_decisions": ((approval_id, "approved"),)})

    tool_request = _next(approved)

    assert tool_request is not None
    assert tool_request.command_type == "RequestActivity"
    assert tool_request.payload["activity_type"] == "tool.call"
    assert tool_request.payload["input_payload"]["tool_call"]["name"] == "write_file"


def test_agent_feeds_tool_result_into_next_model_round_then_completes() -> None:
    running = _after_retrieval(_state(RunFamily.AGENT))
    model_request = _next(running)
    assert model_request is not None
    model_id = UUID(str(model_request.payload["activity_id"]))
    first_result = (
        model_id,
        0,
        "result://model-0",
        "",
        {
            "tool_calls": [
                {
                    "call_id": "call-1",
                    "name": "search_web",
                    "arguments": {"query": "durable execution"},
                    "requires_approval": False,
                    "risk_summary": "Read web",
                }
            ]
        },
    )
    after_model = running.model_copy(
        update={
            "stream_version": 5,
            "settled_activities": (
                *running.settled_activities,
                (model_id, "succeeded", 0),
            ),
            "activity_results": (*running.activity_results, first_result),
        }
    )
    tool_request = _next(after_model)
    assert tool_request is not None
    tool_id = UUID(str(tool_request.payload["activity_id"]))
    after_tool = after_model.model_copy(
        update={
            "stream_version": 8,
            "settled_activities": (
                *after_model.settled_activities,
                (tool_id, "succeeded", 0),
            ),
            "activity_results": (
                *after_model.activity_results,
                (tool_id, 0, "result://tool-0", "ok", {}),
            ),
        }
    )

    second_model = _next(after_tool)

    assert second_model is not None
    assert second_model.payload["activity_type"] == "model.call"
    assert second_model.payload["input_payload"] == {
        "allow_tools": True,
        "history_refs": [
            "result://retrieval",
            "result://model-0",
            "result://tool-0",
        ],
        "round": 1,
    }
    second_model_id = UUID(str(second_model.payload["activity_id"]))
    finished = after_tool.model_copy(
        update={
            "stream_version": 11,
            "settled_activities": (
                *after_tool.settled_activities,
                (second_model_id, "succeeded", 0),
            ),
            "activity_results": (
                *after_tool.activity_results,
                (
                    second_model_id,
                    0,
                    "result://model-1",
                    "answer",
                    {"tool_calls": []},
                ),
            ),
        }
    )

    complete = _next(finished)

    assert complete is not None
    assert complete.command_type == "CompleteRun"
    assert complete.payload == {"result_ref": "result://model-1"}


def test_ask_retrieves_bound_context_before_model_call() -> None:
    running = _state(
        RunFamily.ASK,
        semantic_payload={
            **_state(RunFamily.ASK).semantic_payload,
            "retrieval_required": True,
        },
    )

    retrieval = _next(running)

    assert retrieval is not None
    assert retrieval.payload["activity_type"] == "retrieval.search"
    retrieval_id = UUID(str(retrieval.payload["activity_id"]))
    after_retrieval = running.model_copy(
        update={
            "stream_version": 5,
            "settled_activities": ((retrieval_id, "succeeded", 0),),
            "activity_results": ((retrieval_id, 0, "result://retrieval", "found", {}),),
        }
    )

    model = _next(after_retrieval)

    assert model is not None
    assert model.payload["activity_type"] == "model.call"
    assert model.payload["input_payload"] == {
        "allow_tools": False,
        "history_refs": ["result://retrieval"],
        "round": 0,
    }


@pytest.mark.parametrize(
    ("family", "activity_type"),
    [
        (RunFamily.KB_INGEST, "knowledge.build"),
        (RunFamily.AUTOMATION, "child_run.start"),
        (RunFamily.PATROL, "patrol.execute"),
        (RunFamily.REMEDIATION, "remediation.execute"),
    ],
)
def test_non_conversational_family_has_one_explicit_activity(
    family: RunFamily,
    activity_type: str,
) -> None:
    command = _next(_state(family))

    assert command is not None
    expected = "RequestApproval" if family == RunFamily.REMEDIATION else "RequestActivity"
    assert command.command_type == expected
    if command.command_type == "RequestActivity":
        assert command.payload["activity_type"] == activity_type


def test_patrol_validation_run_has_a_dedicated_kernel_activity() -> None:
    command = _next(
        _state(
            RunFamily.PATROL,
            semantic_payload={
                **_state(RunFamily.PATROL).semantic_payload,
                "operation": "validate",
                "pack_id": "pack-1",
                "pack_version": 3,
                "validation_run_id": "run-validation-1",
            },
        )
    )

    assert command is not None
    assert command.command_type == "RequestActivity"
    assert command.payload["activity_type"] == "patrol.validate"


def test_resource_run_preserves_its_activity_failure_code() -> None:
    running = _state(RunFamily.KB_INGEST)
    request = _next(running)
    assert request is not None
    activity_id = UUID(str(request.payload["activity_id"]))
    failed = running.model_copy(
        update={
            "stream_version": 4,
            "settled_activities": ((activity_id, "failed", 0),),
            "activity_failure_codes": ((activity_id, 0, "KNOWLEDGE_NO_INDEXABLE_SOURCE"),),
        }
    )

    terminal_failure = _next(failed)

    assert terminal_failure is not None
    assert terminal_failure.command_type == "FailRun"
    assert terminal_failure.payload["failure_code"] == "KNOWLEDGE_NO_INDEXABLE_SOURCE"


def test_repeated_activity_failure_exhausts_run_retries() -> None:
    running = _state(
        RunFamily.ASK,
        policy=ExecutionPolicy(agent=AgentExecutionPolicy(max_retries=1)),
        retry_generation=1,
        stream_version=8,
    )
    request = _next(running)
    assert request is not None
    activity_id = UUID(str(request.payload["activity_id"]))
    failed = running.model_copy(
        update={
            "stream_version": 10,
            "settled_activities": ((activity_id, "failed", 1),),
        }
    )

    terminal_failure = _next(failed)

    assert terminal_failure is not None
    assert terminal_failure.command_type == "FailRun"
    assert terminal_failure.payload["retryable"] is False
