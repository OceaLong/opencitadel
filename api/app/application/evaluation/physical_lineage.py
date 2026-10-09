"""Logical model rounds derive from accepted planner identities, never claim IDs."""

from uuid import uuid5

from app.application.execution.activity_types import MODEL_CALL
from app.application.execution.decisions.base import activity_identity
from app.domain.execution.run import RunFamily, RunStatus


def logical_model_invocation(
    state, activity_id, generation, *, root_run_id=None, judge_protocol=None
):
    if (
        state.status != RunStatus.RUNNING
        or generation != state.retry_generation
        or (activity_id, MODEL_CALL, generation) not in state.requested_activities
        or state.policy_snapshot is None
    ):
        raise ValueError("budget_logical_lineage_unavailable")
    if state.family == RunFamily.ASK:
        rounds = (
            3
            if type(judge_protocol) is int
            and judge_protocol == 1
            and state.source_entity_type == "evaluation_judge"
            and state.semantic_payload.get("judge_protocol") == 1
            else 1
        )
    elif state.family == RunFamily.AGENT:
        rounds = state.policy_snapshot.family_policy.agent.max_iterations
    else:
        raise ValueError("budget_logical_lineage_unavailable")
    for index in range(rounds):
        key = f"model:{index}"
        if activity_identity(state, key) == activity_id:
            return uuid5(root_run_id or state.run_id, key)
    raise ValueError("budget_logical_lineage_unavailable")
