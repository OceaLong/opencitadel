"""Pure scheduling protocol; these tests are separate from long-task acceptance."""

from uuid import UUID

import pytest

from app.application.execution.decisions.base import activity_identity
from app.application.execution.decisions.context import context_gate
from app.application.execution.run_context import run_execution_context
from app.domain.execution.context_models import ContextPolicy
from app.domain.execution.definition import HandlerImplementation
from app.domain.execution.run import RunFamily, decision_data_digest
from tests.app.application.execution.test_family_decisions import NOW, _state
from tests.app.domain.execution.test_definition import manifest


def state_for(family):
    definition = manifest(
        family=family,
        context_policy=ContextPolicy(),
        handlers=tuple(
            HandlerImplementation(activity_type=kind, handler_version="1")
            for kind in (
                "context.prepare",
                "context.compact",
                "model.call",
                "retrieval.search",
                "tool.call",
            )
        ),
    )
    return _state(family, definition_pin=definition.pin, definition_manifest=definition)


def gate(state, outcomes, *, round_index=0):
    return context_gate(
        state,
        run_execution_context(state),
        outcomes=outcomes,
        round_index=round_index,
        allow_tools=state.family == RunFamily.AGENT,
        now=NOW,
    )


def complete(state, request, outcomes, data, *, status="succeeded"):
    identity = UUID(request.payload["activity_id"])
    outcomes[identity] = data
    ref = f"execution/results/{identity}/{'d' * 64}.json"
    return state.model_copy(
        update={
            "stream_version": state.stream_version + 3,
            "settled_activities": (*state.settled_activities, (identity, status, 0)),
            "requested_activities": (
                *state.requested_activities,
                (identity, request.payload["activity_type"], 0),
            ),
            "activity_results": (
                *state.activity_results,
                (identity, 0, ref, None, decision_data_digest(data)),
            ),
        }
    )


@pytest.mark.parametrize("family", [RunFamily.AGENT, RunFamily.ASK])
async def test_compact_is_scheduled_individually_and_frozen_context_waits_for_reprepare(family):
    state, outcomes = state_for(family), {}
    first = gate(state, outcomes)
    assert first.command.payload["activity_type"] == "context.prepare"
    assert first.command.payload["input_payload"]["allow_tools"] == (family == RunFamily.AGENT)
    state = complete(
        state,
        first.command,
        outcomes,
        {
            "context_status": "needs_compaction",
            "context_digest": "a" * 64,
            "compaction_chunks": [
                {"ordinal": 0, "digest": "b" * 64},
                {"ordinal": 1, "digest": "c" * 64},
            ],
        },
    )
    for ordinal in range(2):
        result = gate(state, outcomes)
        assert result.pending
        assert result.command.payload["activity_type"] == "context.compact"
        assert result.command.payload["input_payload"]["ordinal"] == ordinal
        assert gate(state, outcomes).command == result.command
        state = complete(state, result.command, outcomes, {"context_status": "compacted"})
    second = gate(state, outcomes)
    assert second.command.payload["activity_type"] == "context.prepare"
    assert second.command.payload["input_payload"]["cycle"] == 1
    state = complete(
        state, second.command, outcomes, {"context_status": "ready", "context_digest": "e" * 64}
    )
    ready = gate(state, outcomes)
    assert not ready.pending
    assert ready.digest == "e" * 64
    assert ready.result_ref


def test_unknown_compact_does_not_schedule_another_provider_call():
    state, outcomes = state_for(RunFamily.AGENT), {}
    state = complete(
        state,
        gate(state, outcomes).command,
        outcomes,
        {
            "context_status": "needs_compaction",
            "context_digest": "a" * 64,
            "compaction_chunks": [{"ordinal": 0, "digest": "b" * 64}],
        },
    )
    state = complete(state, gate(state, outcomes).command, outcomes, {}, status="unknown")
    result = gate(state, outcomes)
    assert result.pending
    assert result.command is None


def test_two_compactions_exhaust_round_budget_instead_of_recursively_resummarizing():
    state, outcomes = state_for(RunFamily.AGENT), {}
    for cycle in range(2):
        prepared = gate(state, outcomes)
        assert prepared.command.payload["activity_type"] == "context.prepare"
        state = complete(
            state,
            prepared.command,
            outcomes,
            {
                "context_status": "needs_compaction",
                "context_digest": "a" * 64,
                "compaction_chunks": [{"ordinal": cycle, "digest": str(cycle + 1) * 64}],
            },
        )
        state = complete(
            state, gate(state, outcomes).command, outcomes, {"context_status": "compacted"}
        )
    prepared = gate(state, outcomes)
    state = complete(
        state,
        prepared.command,
        outcomes,
        {
            "context_status": "needs_compaction",
            "context_digest": "a" * 64,
            "compaction_chunks": [{"ordinal": 2, "digest": "3" * 64}],
        },
    )
    failure = gate(state, outcomes).command
    assert failure.command_type == "FailRun"
    assert failure.payload == {"failure_code": "CONTEXT_COMPACTION_LIMIT", "retryable": False}


def test_prepare_identities_remain_distinct_through_two_hundred_model_rounds():
    state, outcomes = state_for(RunFamily.AGENT), {}
    identities = []
    for round_index in range(200):
        request = gate(state, outcomes, round_index=round_index).command
        identity = UUID(request.payload["activity_id"])
        assert identity == activity_identity(state, f"context:prepare:{round_index}:0")
        identities.append(identity)
    assert len(set(identities)) == 200


async def test_judge_repair_rounds_each_require_their_own_prepared_context():
    from app.application.execution.decisions.ask import next_ask_command

    state, outcomes = state_for(RunFamily.ASK), {}
    state = state.model_copy(
        update={
            "source_entity_type": "evaluation_judge",
            "semantic_payload": {**state.semantic_payload, "judge_protocol": 1},
        }
    )
    for ordinal in range(3):
        prepared = next_ask_command(state, run_execution_context(state), outcomes=outcomes, now=NOW)
        assert prepared.payload["activity_type"] == "context.prepare"
        assert prepared.payload["input_payload"]["round"] == ordinal
        state = complete(
            state,
            prepared,
            outcomes,
            {
                "context_status": "ready",
                "context_digest": "a" * 64,
            },
        )
        model = next_ask_command(state, run_execution_context(state), outcomes=outcomes, now=NOW)
        assert model.payload["activity_type"] == "model.call"
        assert model.payload["input_payload"]["context_ref"]
        assert "history_refs" not in model.payload["input_payload"]
        state = complete(
            state,
            model,
            outcomes,
            {
                "judge_protocol": 1,
                "judge_round": ordinal,
                "judge_status": "invalid",
            },
        )
    failed = next_ask_command(state, run_execution_context(state), outcomes=outcomes, now=NOW)
    assert failed.payload["failure_code"] == "JUDGE_OUTPUT_INVALID"


@pytest.mark.parametrize("iterations", [1, 200])
def test_context_agent_executes_all_tool_rounds_then_one_tools_disabled_answer(iterations):
    from app.application.evaluation.physical_lineage import logical_model_invocation
    from app.application.execution.decisions.agent import next_agent_command
    from app.domain.runtime_policy import AgentExecutionPolicy, ExecutionPolicy
    from app.domain.runtime_policy.snapshot import RunPolicySnapshot
    from tests.app.execution_test_support import run_policy_snapshot_json

    state, outcomes = state_for(RunFamily.AGENT), {}
    state = state.model_copy(
        update={
            "policy_snapshot": RunPolicySnapshot.model_validate(
                run_policy_snapshot_json(
                    RunFamily.AGENT,
                    policy=ExecutionPolicy(agent=AgentExecutionPolicy(max_iterations=iterations)),
                )
            )
        }
    )

    def next_request():
        return next_agent_command(state, run_execution_context(state), outcomes=outcomes, now=NOW)

    state = complete(state, next_request(), outcomes, {})
    for ordinal in range(iterations + 1):
        prepare = next_request()
        assert prepare.payload["activity_type"] == "context.prepare"
        assert prepare.payload["input_payload"]["round"] == ordinal
        assert prepare.payload["input_payload"]["allow_tools"] is (ordinal < iterations)
        state = complete(
            state,
            prepare,
            outcomes,
            {
                "context_status": "ready",
                "context_digest": "a" * 64,
            },
        )
        model = next_request()
        assert model.payload["activity_type"] == "model.call"
        assert model.payload["input_payload"]["allow_tools"] is (ordinal < iterations)
        calls = (
            [
                {
                    "call_id": f"call-{ordinal}-{index}",
                    "name": "read",
                    "arguments": {"index": index},
                    "requires_approval": False,
                    "risk_summary": "read only",
                }
                for index in range(2)
            ]
            if ordinal < iterations
            else []
        )
        state = complete(state, model, outcomes, {"tool_calls": calls})
        # The final call has its own accepted logical identity, including at the cap.
        logical_model_invocation(state, UUID(model.payload["activity_id"]), 0)
        for call in calls:
            tool = next_request()
            assert tool.payload["activity_type"] == "tool.call"
            assert tool.payload["input_payload"]["tool_call"]["call_id"] == call["call_id"]
            state = complete(state, tool, outcomes, {})
    finished = next_request()
    assert finished.command_type == "CompleteRun"
    assert sum(kind == "tool.call" for _, kind, _ in state.requested_activities) == 2 * iterations
    assert sum(kind == "model.call" for _, kind, _ in state.requested_activities) == iterations + 1


def test_final_answer_round_at_maximum_policy_limit_has_valid_summary_identity():
    from uuid import uuid4

    from app.domain.execution.context_summary import ContextCompactBinding, logical_summary_identity

    state = state_for(RunFamily.AGENT)
    binding = ContextCompactBinding(
        prepare_activity_id=uuid4(),
        prepare_ref="object://prepare",
        compaction_digest="a" * 64,
        chunk_digest="b" * 64,
        round_index=1000,
        ordinal=0,
    )
    assert logical_summary_identity(
        state.run_id,
        state.definition_pin,
        round_index=binding.round_index,
        ordinal=binding.ordinal,
        chunk_digest=binding.chunk_digest,
    )
