"""Reusable pure decision primitives for the universal Run aggregate."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from app.domain.execution.commands import JsonValue, RegisteredCommand
from app.domain.execution.run import RunState, RunStatus


class ActivityStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    activity_type: str
    timeout_seconds: int = Field(gt=0, le=86_400)
    input_ref: str | None
    input_digest: str
    input_payload: dict[str, JsonValue] = Field(default_factory=dict)
    requires_approval: bool = False
    approval_kind: str = "external_effect"
    risk_summary: str = "External side effect"


class WorkflowPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    steps: tuple[ActivityStep, ...]


def step(
    activity_type: str,
    semantic: dict[str, JsonValue],
    *,
    timeout_seconds: int,
    input_payload: dict[str, JsonValue] | None = None,
    requires_approval: bool = False,
) -> ActivityStep:
    digest = semantic.get("input_digest")
    input_ref = semantic.get("input_ref")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
        raise TypeError("timeout_seconds must be an integer")
    if not isinstance(digest, str) or not digest:
        raise ValueError("input_digest is required")
    if input_ref is not None and not isinstance(input_ref, str):
        raise ValueError("input_ref must be a string or null")
    return ActivityStep(
        activity_type=activity_type,
        timeout_seconds=timeout_seconds,
        input_ref=input_ref,
        input_digest=digest,
        input_payload=input_payload or {},
        requires_approval=requires_approval,
        approval_kind="external_effect",
        risk_summary=f"Authorize {activity_type}",
    )


def lifecycle_command(state: RunState) -> tuple[bool, RegisteredCommand | None]:
    """Return whether common lifecycle state fully determines the next action."""
    if state.status in {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }:
        return True, None
    if state.status == RunStatus.QUEUED:
        return True, command(state, "StartRun", {})
    if state.status == RunStatus.WAITING:
        # WAITING(retry) is idle here on purpose (D6): RetryRun is delivered by
        # the durable backoff timer scheduled in run.py's _decide_FailRun, not
        # by the decision loop — otherwise every retryable failure retried with
        # zero delay (retry storm). WAITING(approval) resumes via
        # DecideApproval / ExpireApproval commands.
        return True, None
    if state.status != RunStatus.RUNNING or state.active_activity_ids:
        return True, None
    return False, None


def next_plan_command(
    state: RunState,
    plan: WorkflowPlan,
    *,
    now: datetime,
    max_retries: int,
) -> RegisteredCommand | None:
    handled, lifecycle = lifecycle_command(state)
    if handled:
        return lifecycle

    for index, activity in enumerate(plan.steps):
        key = f"step:{index}:{activity.activity_type}"
        activity_id = activity_identity(state, key)
        status = settled_status(state, activity_id)
        if status is None:
            if activity.requires_approval:
                approval = approval_identity(state, key)
                decision = approval_decision(state, approval)
                if decision is None:
                    return request_approval(
                        state,
                        activity_id=activity_id,
                        approval_id=approval,
                        approval_kind=activity.approval_kind,
                        risk_summary=activity.risk_summary,
                        subject_label=activity.activity_type,
                    )
            return request_activity(
                state,
                activity_id=activity_id,
                activity_type=activity.activity_type,
                now=now,
                timeout_seconds=activity.timeout_seconds,
                input_ref=activity.input_ref,
                input_digest=activity.input_digest,
                input_payload=activity.input_payload,
            )
        if status != "succeeded":
            return fail_for_activity(
                state,
                status,
                activity_id=activity_id,
                max_retries=max_retries,
            )
    return command(state, "CompleteRun", {"result_ref": last_result_ref(state)})


def request_activity(
    state: RunState,
    *,
    activity_id: UUID,
    activity_type: str,
    now: datetime,
    timeout_seconds: int,
    input_ref: str | None,
    input_digest: str,
    input_payload: dict[str, JsonValue],
    public_data: dict[str, JsonValue] | None = None,
) -> RegisteredCommand:
    return command(
        state,
        "RequestActivity",
        {
            "activity_id": str(activity_id),
            "activity_type": activity_type,
            "timeout_at": (now + timedelta(seconds=timeout_seconds)).isoformat(),
            "input_ref": input_ref,
            "input_digest": input_digest,
            "input_payload": input_payload,
            "public_data": public_data or {},
        },
    )


def request_approval(
    state: RunState,
    *,
    activity_id: UUID,
    approval_id: UUID,
    approval_kind: str,
    risk_summary: str,
    subject_label: str,
    choices: list[str] | None = None,
) -> RegisteredCommand:
    payload: dict[str, JsonValue] = {
        "approval_id": str(approval_id),
        "subject_activity_id": str(activity_id),
        "approval_kind": approval_kind,
        "risk_summary": risk_summary[:1024],
        "subject_label": subject_label[:128],
    }
    if choices:
        # Clarification card options (already normalized by model.call).
        payload["choices"] = [choice[:200] for choice in choices[:6]]
    return command(state, "RequestApproval", payload)


def approval_decision(
    state: RunState,
    approval_id: UUID,
) -> tuple[str, str] | None:
    """One approval's (decision, feedback), or None while still pending."""
    return next(
        (
            (decision, feedback)
            for decided_id, decision, feedback in state.approval_decisions
            if decided_id == approval_id
        ),
        None,
    )


def fail_for_activity(
    state: RunState,
    status: str,
    *,
    activity_id: UUID | None = None,
    max_retries: int,
) -> RegisteredCommand:
    if not isinstance(max_retries, int) or isinstance(max_retries, bool):
        raise TypeError("max_retries must be an integer")
    if max_retries < 0 or max_retries > 10:
        raise ValueError("max_retries must be between 0 and 10")
    return command(
        state,
        "FailRun",
        {
            "failure_code": (
                activity_failure_code(state, activity_id) if activity_id is not None else None
            )
            or f"ACTIVITY_{status.upper()}",
            "retryable": status == "failed" and state.retry_generation < max_retries,
        },
    )


def activity_failure_code(state: RunState, activity_id: UUID) -> str | None:
    return next(
        (
            failure_code
            for candidate, generation, failure_code in state.activity_failure_codes
            if candidate == activity_id and generation == state.retry_generation
        ),
        None,
    )


def activity_identity(state: RunState, key: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"opencitadel:{state.run_id}:activity:{state.retry_generation}:{key}",
    )


def approval_identity(state: RunState, key: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"opencitadel:{state.run_id}:approval:{state.retry_generation}:{key}",
    )


def settled_status(state: RunState, activity_id: UUID) -> str | None:
    return next(
        (
            status
            for candidate, status, generation in state.settled_activities
            if candidate == activity_id and generation == state.retry_generation
        ),
        None,
    )


def activity_result(
    state: RunState,
    activity_id: UUID,
    *,
    outcomes: Mapping[UUID, dict[str, JsonValue]] | None = None,
) -> tuple[str | None, str | None, dict[str, JsonValue]] | None:
    """One settled activity's (result_ref, result_summary, decision_data).

    The ref and summary live in the aggregate state; the decision payload is
    off-stream and must be supplied via ``outcomes`` (digest-verified by the
    decision source). A recorded digest with no supplied payload is a wiring
    error and fails loud rather than silently planning without tool calls.
    """
    for candidate, generation, result_ref, summary, digest in state.activity_results:
        if candidate == activity_id and generation == state.retry_generation:
            decision_data = (outcomes or {}).get(activity_id)
            if digest is not None and decision_data is None:
                raise ValueError(
                    f"activity {activity_id} recorded a decision digest but no "
                    "decision payload was supplied to the planner"
                )
            return result_ref, summary, decision_data or {}
    return None


def result_refs(state: RunState) -> list[str]:
    return [
        result_ref
        for _, generation, result_ref, _, _ in state.activity_results
        if generation == state.retry_generation and result_ref is not None
    ]


def last_result_ref(state: RunState) -> str | None:
    refs = result_refs(state)
    return refs[-1] if refs else None


def command(
    state: RunState,
    command_type: str,
    payload: dict[str, JsonValue],
) -> RegisteredCommand:
    return RegisteredCommand(
        command_id=uuid5(
            NAMESPACE_URL,
            f"opencitadel:{state.run_id}:{state.stream_version}:{command_type}",
        ),
        command_type=command_type,
        run_id=state.run_id,
        expected_stream_version=state.stream_version,
        payload=payload,
    )


__all__ = [
    "ActivityStep",
    "WorkflowPlan",
    "activity_identity",
    "activity_result",
    "approval_decision",
    "approval_identity",
    "command",
    "fail_for_activity",
    "last_result_ref",
    "lifecycle_command",
    "next_plan_command",
    "request_activity",
    "request_approval",
    "result_refs",
    "settled_status",
    "step",
]
