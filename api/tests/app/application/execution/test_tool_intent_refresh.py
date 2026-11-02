from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.execution.activities.tool_call import ToolCallActivityHandler
from app.application.execution.activities.tool_intent_refresh import (
    ToolIntentRefreshActivityHandler,
)
from app.application.execution.decisions import next_command
from app.application.execution.decisions.base import activity_identity, approval_identity
from app.application.execution.invocation_contracts import finalize_subject
from app.application.execution.run_context import run_execution_context
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.effect import EffectOperationSpec, EffectState
from app.domain.execution.events import StoredEvent
from app.domain.execution.run import InvalidRunTransitionError, RunAggregate, decision_data_digest
from app.domain.execution.tool_contract import ToolApprovalSubject
from tests.app.application.execution.test_clarification_flow import NOW, _clarifying_model_round
from tests.app.application.execution.test_invocation_contracts import _setup, _Write


def envelope(state, name, payload, version=1):
    return CommandEnvelope(
        command_id=uuid4(),
        command_type=name,
        command_schema_version=version,
        stream_type="run",
        stream_id=str(state.run_id),
        expected_stream_version=None,
        owner_user_id=state.owner_user_id,
        team_id=state.team_id,
        correlation_id=state.run_id,
        causation_id=None,
        issued_at=NOW,
        payload=payload,
    )


def apply(state, command):
    aggregate = RunAggregate()
    decision = aggregate.decide(state, command)
    for event in decision.events:
        state = aggregate.evolve(
            state,
            StoredEvent(
                **event.model_dump(),
                position=state.stream_version + 1,
                event_id=uuid4(),
                stream_type="run",
                stream_id=str(state.run_id),
                stream_version=state.stream_version + 1,
                owner_user_id=state.owner_user_id,
                team_id=state.team_id,
                correlation_id=state.run_id,
                causation_id=command.command_id,
                occurred_at=NOW,
                prev_hash="0" * 64,
                event_hash="1" * 64,
            ),
        )
    return state, decision


async def waiting_intent():
    pack = _Write()
    context, _, catalog, _, objects, old_request = await _setup(pack)
    state, model_id, data, _ = _clarifying_model_round()
    old = ToolApprovalSubject.model_validate(old_request.input_payload["subject"])
    contract = await objects.load_input(
        key=old.contract_ref, expected_digest=old.contract_object_digest
    )
    ref, digest = await objects.put_input(state.run_id, contract)
    subject = finalize_subject(
        old.model_copy(
            update={
                "originating_activity_id": model_id,
                "contract_ref": ref,
                "contract_object_digest": digest,
            }
        ),
        {"key": "original"},
    )
    call = {
        **old_request.input_payload["tool_call"],
        "requires_approval": True,
        "risk_summary": "Write target",
        "subject": subject.model_dump(mode="json"),
    }
    data["tool_calls"] = [call]
    tool_key = "tool:0:0:call-1"
    activity = activity_identity(state, tool_key)
    state = state.model_copy(
        update={
            "status": "waiting",
            "wait_reason": "contract_changed",
            "semantic_payload": {
                "input_ref": old_request.input_ref,
                "input_digest": old_request.input_digest,
            },
            "settled_activities": (*state.settled_activities, (activity, "failed", 0)),
            "activity_failure_codes": ((activity, 0, "TOOL_CONTRACT_CHANGED"),),
            "activity_subjects": ((activity, 0, subject.digest),),
            "approval_decisions": ((approval_identity(state, tool_key), "approved", ""),),
            "activity_results": tuple(
                (a, g, r, s, decision_data_digest(data) if a == model_id else d)
                for a, g, r, s, d in state.activity_results
            ),
        }
    )
    context = context.model_copy(update={"run": run_execution_context(state)})
    return state, model_id, data, subject, activity, pack, catalog, objects, context


@pytest.mark.asyncio
async def test_refresh_preserves_completed_work_and_requires_a_new_exact_approval():
    (
        state,
        model_id,
        data,
        subject,
        activity,
        pack,
        catalog,
        objects,
        context,
    ) = await waiting_intent()
    request_id = uuid4()
    command = envelope(
        state,
        "RefreshToolIntent",
        {
            "refresh_id": str(request_id),
            "activity_id": str(activity),
            "expected_subject_digest": subject.digest,
            "actor_user_id": "user-1",
            "reason": "Reviewed changed contract",
        },
    )
    state, _ = apply(state, command)
    assert not RunAggregate().decide(state, command).events
    renewal = state.tool_intent_refreshes[-1]
    prepare = next_command(state, run_execution_context(state), outcomes={model_id: data}, now=NOW)
    assert prepare.payload["activity_id"] == str(renewal.prepare_activity_id)
    assert prepare.payload["activity_type"] == "tool.intent_refresh"
    state, decision = apply(
        state,
        envelope(state, prepare.command_type, prepare.payload, prepare.command_schema_version),
    )
    original = pack.get_tool_descriptors()[0]
    pack.get_tool_descriptors = lambda: [
        replace(original, schema={**original.schema, "description": "Revised tool"})
    ]
    outcome = await ToolIntentRefreshActivityHandler(objects=objects, tools=catalog).execute(
        decision.activity_requests[0], context
    )
    assert outcome.status == "succeeded"
    assert pack.sends == []
    renewed = ToolApprovalSubject.model_validate(outcome.decision_data["tool_call"]["subject"])
    assert renewed.invocation_id != subject.invocation_id
    assert renewed.arguments_digest == subject.arguments_digest
    assert renewed.contract_digest != subject.contract_digest
    state = state.model_copy(
        update={
            "active_activity_ids": (),
            "settled_activities": (
                *state.settled_activities,
                (renewal.prepare_activity_id, "succeeded", 0),
            ),
            "activity_results": (
                *state.activity_results,
                (
                    renewal.prepare_activity_id,
                    0,
                    outcome.result_ref,
                    None,
                    decision_data_digest(outcome.decision_data),
                ),
            ),
        }
    )
    outcomes = {model_id: data, renewal.prepare_activity_id: outcome.decision_data}
    approval = next_command(state, run_execution_context(state), outcomes=outcomes, now=NOW)
    assert approval.command_type == "RequestApproval"
    assert approval.payload["subject_digest"] == renewed.digest
    assert approval.payload["public_preview"]["arguments_json"] == '{"key":"original"}'
    state, _ = apply(
        state,
        envelope(state, approval.command_type, approval.payload, approval.command_schema_version),
    )
    with pytest.raises(InvalidRunTransitionError, match="subject"):
        RunAggregate().decide(
            state,
            envelope(
                state,
                "DecideApproval",
                {
                    "approval_id": approval.payload["approval_id"],
                    "decision": "approved",
                    "actor_user_id": "user-1",
                    "expected_subject_digest": subject.digest,
                },
                2,
            ),
        )
    state, _ = apply(
        state,
        envelope(
            state,
            "DecideApproval",
            {
                "approval_id": approval.payload["approval_id"],
                "decision": "approved",
                "actor_user_id": "user-1",
                "expected_subject_digest": renewed.digest,
            },
            2,
        ),
    )
    execute = next_command(state, run_execution_context(state), outcomes=outcomes, now=NOW)
    assert execute.payload["activity_id"] == str(renewal.replacement_activity_id)
    assert execute.payload["invocation_id"] == str(renewed.invocation_id)
    assert execute.payload["input_payload"]["tool_call"]["arguments"] == {"key": "original"}
    assert (activity, "failed", 0) in state.settled_activities
    assert (model_id, "succeeded", 0) in state.settled_activities
    _, execution = apply(
        state,
        envelope(state, execute.command_type, execute.payload, execute.command_schema_version),
    )
    async with catalog._uow_factory() as uow:

        async def invoke(spec, passed_context, send, *, preflight):
            await preflight(uow)
            return SimpleNamespace(state="applied", result=await send())

        completed = await ToolCallActivityHandler(
            objects=objects,
            tools=catalog,
            effects=SimpleNamespace(recover=AsyncMock(return_value=None), invoke=invoke),
        ).execute(execution.activity_requests[0], context)
    assert completed.status == "succeeded"
    assert pack.sends == ["original"]
    state = state.model_copy(
        update={
            "settled_activities": (
                *state.settled_activities,
                (renewal.replacement_activity_id, "succeeded", 0),
            ),
            "activity_results": (
                *state.activity_results,
                (
                    renewal.replacement_activity_id,
                    0,
                    completed.result_ref,
                    None,
                    decision_data_digest(completed.decision_data),
                ),
            ),
        }
    )
    next_model = next_command(state, run_execution_context(state), outcomes=outcomes, now=NOW)
    assert next_model.payload["activity_type"] == "model.call"
    assert completed.result_ref in next_model.payload["input_payload"]["history_refs"]
    assert outcome.result_ref not in next_model.payload["input_payload"]["history_refs"]


@pytest.mark.asyncio
async def test_failed_preparation_can_be_refreshed_without_replaying_old_work():
    (
        state,
        model_id,
        data,
        subject,
        activity,
        pack,
        catalog,
        objects,
        context,
    ) = await waiting_intent()
    payload = {
        "refresh_id": str(uuid4()),
        "activity_id": str(activity),
        "expected_subject_digest": subject.digest,
        "actor_user_id": "user-1",
        "reason": "Reviewed",
    }
    state, _ = apply(state, envelope(state, "RefreshToolIntent", payload))
    first = state.tool_intent_refreshes[-1]
    prepare = next_command(state, run_execution_context(state), outcomes={model_id: data}, now=NOW)
    state, decision = apply(
        state,
        envelope(state, prepare.command_type, prepare.payload, prepare.command_schema_version),
    )
    catalog._build.side_effect = PermissionError("connector access was revoked again")
    failed = await ToolIntentRefreshActivityHandler(objects=objects, tools=catalog).execute(
        decision.activity_requests[0], context
    )
    assert failed.failure_code == "TOOL_INTENT_REFRESH_FAILED"
    assert pack.sends == []
    state = state.model_copy(
        update={
            "active_activity_ids": (),
            "settled_activities": (
                *state.settled_activities,
                (first.prepare_activity_id, "failed", 0),
            ),
            "activity_failure_codes": (
                *state.activity_failure_codes,
                (first.prepare_activity_id, 0, failed.failure_code),
            ),
        }
    )
    wait = next_command(state, run_execution_context(state), outcomes={model_id: data}, now=NOW)
    assert wait.command_type == "WaitRun"
    state, _ = apply(state, envelope(state, wait.command_type, wait.payload))
    state, _ = apply(
        state,
        envelope(
            state,
            "RefreshToolIntent",
            {
                **payload,
                "refresh_id": str(uuid4()),
                "activity_id": str(first.prepare_activity_id),
            },
        ),
    )
    retry = next_command(state, run_execution_context(state), outcomes={model_id: data}, now=NOW)
    assert retry.payload["activity_type"] == "tool.intent_refresh"
    assert retry.payload["activity_id"] != str(first.prepare_activity_id)
    assert retry.payload["input_payload"]["tool_call"]["arguments"] == {"key": "original"}
    assert state.retry_generation == 0
    assert (model_id, "succeeded", 0) in state.settled_activities


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["wrong_subject", "unknown", "terminal", "already_superseded"])
async def test_refresh_rejects_stale_or_uncertain_intent(change):
    state, _, _, subject, activity, *_ = await waiting_intent()
    payload = {
        "refresh_id": str(uuid4()),
        "activity_id": str(activity),
        "expected_subject_digest": subject.digest,
        "actor_user_id": "user-1",
        "reason": "Retry review",
    }
    if change == "wrong_subject":
        payload["expected_subject_digest"] = "0" * 64
    if change == "unknown":
        state = state.model_copy(update={"wait_reason": "external_outcome_unknown"})
    if change == "terminal":
        state = state.model_copy(update={"status": "cancelled"})
    if change == "already_superseded":
        state, _ = apply(state, envelope(state, "RefreshToolIntent", payload))
        state = state.model_copy(update={"status": "waiting", "wait_reason": "contract_changed"})
        payload = {**payload, "refresh_id": str(uuid4())}
    with pytest.raises(InvalidRunTransitionError):
        RunAggregate().decide(state, envelope(state, "RefreshToolIntent", payload))


@pytest.mark.asyncio
@pytest.mark.parametrize("effect_state", ["sending", "pending", "unknown", "applied"])
async def test_effect_fact_blocks_renewal_even_when_wait_reason_says_contract_drift(effect_state):
    state, _, _, subject, activity, *_ = await waiting_intent()
    effect = EffectState(
        spec=EffectOperationSpec(
            operation_id=subject.invocation_id,
            invocation_id=subject.invocation_id,
            run_id=state.run_id,
            activity_id=activity,
            generation=0,
            provider_identity="write",
            contract_digest=subject.contract_digest,
            arguments_digest=subject.arguments_digest,
            target_digest=subject.target_digest,
            idempotency_key=f"inv:{subject.invocation_id}",
            capability="manual_only",
        ),
        state=effect_state,
    )
    state = state.model_copy(update={"effect_operations": (effect,)})
    assert state.wait_reason == "contract_changed"
    with pytest.raises(InvalidRunTransitionError, match="effect must be reconciled"):
        RunAggregate().decide(
            state,
            envelope(
                state,
                "RefreshToolIntent",
                {
                    "refresh_id": str(uuid4()),
                    "activity_id": str(activity),
                    "expected_subject_digest": subject.digest,
                    "actor_user_id": "user-1",
                    "reason": "Reviewed",
                },
            ),
        )
