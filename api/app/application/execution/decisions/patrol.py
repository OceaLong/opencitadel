"""Patrol and remediation workflows."""

from app.application.execution import activity_types
from app.application.execution.decisions.base import WorkflowPlan, step
from app.domain.execution.commands import JsonValue
from app.domain.execution.run import RunFamily


def patrol_plan(
    family: RunFamily,
    semantic: dict[str, JsonValue],
    *,
    timeout_seconds: int,
) -> WorkflowPlan:
    if family == RunFamily.PATROL:
        types = (
            activity_types.PATROL_VALIDATE
            if semantic.get("operation") == "validate"
            else activity_types.PATROL_EXECUTE,
        )
    elif family == RunFamily.REMEDIATION:
        types = (activity_types.REMEDIATION_EXECUTE,)
    else:
        raise ValueError(f"not a patrol family: {family}")
    return WorkflowPlan(
        steps=tuple(
            step(
                item,
                semantic,
                timeout_seconds=timeout_seconds,
                requires_approval=family == RunFamily.REMEDIATION,
            )
            for item in types
        )
    )


__all__ = ["patrol_plan"]
