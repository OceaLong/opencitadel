"""Patrol and remediation workflows."""

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
            "patrol.validate" if semantic.get("operation") == "validate" else "patrol.execute",
        )
    elif family == RunFamily.REMEDIATION:
        types = ("remediation.execute",)
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
