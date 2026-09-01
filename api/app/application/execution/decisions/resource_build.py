"""Knowledge-base build workflow."""

from app.application.execution.decisions.base import WorkflowPlan, step
from app.domain.execution.commands import JsonValue
from app.domain.execution.run import RunFamily


def resource_build_plan(
    family: RunFamily,
    semantic: dict[str, JsonValue],
    *,
    timeout_seconds: int,
) -> WorkflowPlan:
    if family is not RunFamily.KB_INGEST:
        raise ValueError(f"not a resource build family: {family}")
    return WorkflowPlan(steps=(step("knowledge.build", semantic, timeout_seconds=timeout_seconds),))


__all__ = ["resource_build_plan"]
