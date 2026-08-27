"""Production decision registry for every Run family."""

from datetime import datetime

from app.application.execution.decisions.agent import next_agent_command
from app.application.execution.decisions.ask import next_ask_command
from app.application.execution.decisions.automation import automation_plan
from app.application.execution.decisions.base import next_plan_command
from app.application.execution.decisions.patrol import patrol_plan
from app.application.execution.decisions.resource_build import resource_build_plan
from app.domain.execution.commands import RegisteredCommand
from app.domain.execution.context import RunExecutionContext
from app.domain.execution.run import RunFamily, RunState


def next_command(
    state: RunState,
    context: RunExecutionContext,
    *,
    now: datetime,
) -> RegisteredCommand | None:
    if state.family is None:
        raise ValueError("Run family is required")
    if state.run_id != context.run_id or state.family != context.family:
        raise ValueError("Run context does not match decision state")
    activity_policy = context.policy_snapshot.common.activity
    agent_policy = getattr(context.policy_snapshot.family_policy, "agent", None)
    max_retries = agent_policy.max_retries if agent_policy is not None else 0
    if state.family == RunFamily.AGENT:
        return next_agent_command(state, context, now=now)
    if state.family == RunFamily.ASK:
        return next_ask_command(state, context, now=now)
    if state.family in {RunFamily.KB_INGEST, RunFamily.CODEBASE_INGEST}:
        plan = resource_build_plan(
            state.family,
            state.semantic_payload,
            timeout_seconds=activity_policy.tool_timeout_seconds,
        )
    elif state.family == RunFamily.AUTOMATION:
        plan = automation_plan(
            state.semantic_payload,
            timeout_seconds=activity_policy.tool_timeout_seconds,
        )
    elif state.family in {RunFamily.PATROL, RunFamily.REMEDIATION}:
        plan = patrol_plan(
            state.family,
            state.semantic_payload,
            timeout_seconds=activity_policy.tool_timeout_seconds,
        )
    else:
        raise ValueError(f"unsupported Run family: {state.family}")
    return next_plan_command(
        state,
        plan,
        now=now,
        max_retries=max_retries,
    )


__all__ = ["next_command"]
