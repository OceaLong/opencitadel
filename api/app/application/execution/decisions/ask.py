"""Pure retrieval-then-answer decisions for Ask Runs."""

from datetime import datetime

from app.application.execution.decisions.base import (
    activity_identity,
    activity_result,
    command,
    fail_for_activity,
    lifecycle_command,
    request_activity,
    result_refs,
    settled_status,
)
from app.domain.execution.context import RunExecutionContext
from app.domain.execution.run import RunState


def next_ask_command(
    state: RunState,
    context: RunExecutionContext,
    *,
    now: datetime,
):
    handled, lifecycle = lifecycle_command(state)
    if handled:
        return lifecycle
    semantic = state.semantic_payload
    policy = context.policy_snapshot.family_policy
    if policy.kind != "ask":
        raise ValueError("Ask decision requires Ask Run policy")
    timeout = context.policy_snapshot.common.activity.tool_timeout_seconds
    max_retries = policy.agent.max_retries
    input_ref = semantic.get("input_ref")
    input_digest = semantic.get("input_digest")
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        raise TypeError("timeout_seconds must be an integer")
    if input_ref is not None and not isinstance(input_ref, str):
        raise TypeError("input_ref must be a string or null")
    if not isinstance(input_digest, str) or not input_digest:
        raise ValueError("input_digest is required")

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
        return fail_for_activity(state, retrieval_status, max_retries=max_retries)

    model_id = activity_identity(state, "model:0")
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
                "allow_tools": False,
                "history_refs": result_refs(state),
                "round": 0,
            },
        )
    if model_status != "succeeded":
        return fail_for_activity(state, model_status, max_retries=max_retries)
    result = activity_result(state, model_id)
    if result is None:
        return command(
            state,
            "FailRun",
            {"failure_code": "MODEL_RESULT_MISSING", "retryable": False},
        )
    return command(state, "CompleteRun", {"result_ref": result[0]})


__all__ = ["next_ask_command"]
