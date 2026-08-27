"""Internal idempotent tool used only by Patrol sessions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.domain.models.patrol import PatrolObservationSubmission
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, tool

FinalizePatrolFn = Callable[..., Awaitable[dict[str, Any]]]
PATROL_SUBMIT_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.WORKSPACE_WRITE,
    idempotency=ToolIdempotency.IDEMPOTENT_WITH_KEY,
    approval=ApprovalMode.NEVER,
    concurrency_group="patrol-run",
)


class PatrolTool(BaseTool):
    name = "patrol"

    def __init__(self, finalize_fn: FinalizePatrolFn, *, session_id: str) -> None:
        super().__init__()
        self._finalize_fn = finalize_fn
        self._session_id = session_id

    @tool(
        name="patrol_submit_results",
        description="Submit structured observations for the current Ops Patrol Run. Final status is computed by the server.",
        parameters={
            "run_id": {"type": "string"},
            "idempotency_key": {"type": "string"},
            "collector_capability_hash": {"type": "string"},
            "results": {"type": "array", "items": {"type": "object"}},
        },
        required=["run_id", "idempotency_key", "collector_capability_hash", "results"],
        policy=PATROL_SUBMIT_POLICY,
    )
    async def patrol_submit_results(
        self,
        run_id: str,
        idempotency_key: str,
        collector_capability_hash: str,
        results: list[dict[str, Any]],
    ) -> ToolResult:
        try:
            data = await self._finalize_fn(
                run_id=run_id,
                session_id=self._session_id,
                idempotency_key=idempotency_key,
                collector_capability_hash=collector_capability_hash,
                submissions=[PatrolObservationSubmission.model_validate(item) for item in results],
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return ToolResult(success=False, message=str(exc))
        return ToolResult(success=True, data=data, message="Patrol results finalized")
