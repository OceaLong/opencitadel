"""Scheduled automation workflow."""

from app.application.execution import activity_types
from app.application.execution.decisions.base import WorkflowPlan, step
from app.domain.execution.commands import JsonValue


def automation_plan(
    semantic: dict[str, JsonValue],
    *,
    timeout_seconds: int,
) -> WorkflowPlan:
    return WorkflowPlan(
        steps=(
            step(
                activity_types.CHILD_RUN_START,
                semantic,
                timeout_seconds=timeout_seconds,
            ),
        )
    )


__all__ = ["automation_plan"]
