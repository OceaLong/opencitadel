"""Knowledge-base and codebase build workflows."""

from app.application.execution.decisions.base import WorkflowPlan, step
from app.domain.execution.commands import JsonValue
from app.domain.execution.run import RunFamily


def resource_build_plan(
    family: RunFamily,
    semantic: dict[str, JsonValue],
    *,
    timeout_seconds: int,
) -> WorkflowPlan:
    if family == RunFamily.KB_INGEST:
        types = ("knowledge.build",)
    elif family == RunFamily.CODEBASE_INGEST:
        types = ("codebase.build",)
    else:
        raise ValueError(f"not a resource build family: {family}")
    return WorkflowPlan(
        steps=tuple(step(item, semantic, timeout_seconds=timeout_seconds) for item in types)
    )


__all__ = ["resource_build_plan"]
