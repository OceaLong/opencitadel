"""Pure multi-round model/tool decisions for Agent Runs."""

from __future__ import annotations

from datetime import datetime

from app.application.execution.decisions.base import (
    activity_identity,
    activity_result,
    approval_identity,
    command,
    fail_for_activity,
    lifecycle_command,
    request_activity,
    request_approval,
    result_refs,
    settled_status,
)
from app.domain.execution.commands import JsonValue
from app.domain.execution.context import RunExecutionContext
from app.domain.execution.run import RunState


def next_agent_command(
    state: RunState,
    context: RunExecutionContext,
    *,
    now: datetime,
):
    handled, lifecycle = lifecycle_command(state)
    if handled:
        return lifecycle
    policy = context.policy_snapshot.family_policy
    if policy.kind != "agent":
        raise ValueError("Agent decision requires Agent Run policy")
    max_iterations = policy.agent.max_iterations
    max_retries = policy.agent.max_retries
    timeout = context.policy_snapshot.common.activity.tool_timeout_seconds
    input_ref, input_digest = _input_identity(state)

    retrieval_id = activity_identity(state, "retrieval:0")
    retrieval_status = settled_status(state, retrieval_id)
    if retrieval_status is None:
        return request_activity(
            state,
            activity_id=retrieval_id,
            activity_type="retrieval.search",
            now=now,
            timeout_seconds=timeout,
            input_ref=input_ref,
            input_digest=input_digest,
            input_payload={},
        )
    if retrieval_status != "succeeded":
        return fail_for_activity(
            state,
            retrieval_status,
            activity_id=retrieval_id,
            max_retries=max_retries,
        )

    for round_index in range(max_iterations):
        model_key = f"model:{round_index}"
        model_id = activity_identity(state, model_key)
        model_status = settled_status(state, model_id)
        if model_status is None:
            return request_activity(
                state,
                activity_id=model_id,
                activity_type="model.call",
                now=now,
                timeout_seconds=timeout,
                input_ref=input_ref,
                input_digest=input_digest,
                input_payload={
                    "allow_tools": True,
                    "history_refs": result_refs(state),
                    "round": round_index,
                },
            )
        if model_status != "succeeded":
            return fail_for_activity(
                state,
                model_status,
                activity_id=model_id,
                max_retries=max_retries,
            )
        model_result = activity_result(state, model_id)
        if model_result is None:
            return command(
                state,
                "FailRun",
                {"failure_code": "MODEL_RESULT_MISSING", "retryable": False},
            )
        model_ref, _, decision_data = model_result
        calls = decision_data.get("tool_calls", [])
        if not isinstance(calls, list):
            return _invalid_tool_decision(state)
        if not calls:
            return command(state, "CompleteRun", {"result_ref": model_ref})
        if round_index + 1 >= max_iterations:
            return command(
                state,
                "FailRun",
                {"failure_code": "AGENT_ITERATION_LIMIT", "retryable": False},
            )
        if len(calls) > 16:
            return _invalid_tool_decision(state)
        for call_index, raw_call in enumerate(calls):
            call = _tool_call(raw_call)
            if call is None:
                return _invalid_tool_decision(state)
            call_id, name, arguments, requires_approval, risk_summary = call
            tool_key = f"tool:{round_index}:{call_index}:{call_id}"
            tool_id = activity_identity(state, tool_key)
            tool_status = settled_status(state, tool_id)
            if tool_status is None:
                if requires_approval:
                    approval_id = approval_identity(state, tool_key)
                    decision = dict(state.approval_decisions).get(approval_id)
                    if decision is None:
                        return request_approval(
                            state,
                            activity_id=tool_id,
                            approval_id=approval_id,
                            approval_kind="tool_effect",
                            risk_summary=risk_summary,
                            subject_label=name,
                        )
                return request_activity(
                    state,
                    activity_id=tool_id,
                    activity_type="tool.call",
                    now=now,
                    timeout_seconds=timeout,
                    input_ref=input_ref,
                    input_digest=input_digest,
                    input_payload={
                        "round": round_index,
                        "tool_call": {
                            "call_id": call_id,
                            "name": name,
                            "arguments": arguments,
                        },
                    },
                    public_data={
                        "tool_call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                    },
                )
            if tool_status != "succeeded":
                return fail_for_activity(
                    state,
                    tool_status,
                    activity_id=tool_id,
                    max_retries=max_retries,
                )
    return command(
        state,
        "FailRun",
        {"failure_code": "AGENT_ITERATION_LIMIT", "retryable": False},
    )


def _input_identity(state: RunState) -> tuple[str | None, str]:
    input_ref = state.semantic_payload.get("input_ref")
    input_digest = state.semantic_payload.get("input_digest")
    if input_ref is not None and not isinstance(input_ref, str):
        raise TypeError("input_ref must be a string or null")
    if not isinstance(input_digest, str) or not input_digest:
        raise ValueError("input_digest is required")
    return input_ref, input_digest


def _tool_call(
    raw: JsonValue,
) -> tuple[str, str, dict[str, JsonValue], bool, str] | None:
    if not isinstance(raw, dict):
        return None
    call_id = raw.get("call_id")
    name = raw.get("name")
    arguments = raw.get("arguments")
    requires_approval = raw.get("requires_approval")
    risk_summary = raw.get("risk_summary")
    if not isinstance(call_id, str) or not call_id.strip():
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(arguments, dict):
        return None
    if not isinstance(requires_approval, bool):
        return None
    if not isinstance(risk_summary, str) or not risk_summary.strip():
        return None
    return call_id, name, arguments, requires_approval, risk_summary


def _invalid_tool_decision(state: RunState):
    return command(
        state,
        "FailRun",
        {"failure_code": "MODEL_TOOL_DECISION_INVALID", "retryable": False},
    )


__all__ = ["next_agent_command"]
