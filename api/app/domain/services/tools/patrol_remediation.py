"""Internal, narrow tool exposed only inside an Ops Patrol Remediation session.

Structure mirrors app/domain/services/tools/patrol.py: a single-method tool
pack whose method delegates every real validation to the server-side service
(PatrolRemediationService.execute), returning only a ToolResult back to the
governed agent loop. The `idempotency_key` parameter is declared explicitly
so the batch executor's idempotency-key contract
(ToolBatchExecutor._supports_idempotency_key: IDEMPOTENT_WITH_KEY policy +
"idempotency_key" in both the tool schema and the method signature) is
satisfied — but the value that ends up here is the batch executor's own
stable per-call key (session_id + tool_call_id + args_hash), not anything the
LLM can choose. PatrolRemediationService.execute() never forwards this
argument to the Actuator; it always uses the persisted
PatrolRemediation.idempotency_key captured at proposal time.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.domain.models.tool_policy import ApprovalMode, ToolCapability, ToolEffect, ToolExecutionPolicy, ToolIdempotency
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, tool


ExecuteRemediationFn = Callable[..., Awaitable[dict[str, Any]]]

PATROL_REMEDIATION_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.EXTERNAL_WRITE,
    idempotency=ToolIdempotency.IDEMPOTENT_WITH_KEY,
    approval=ApprovalMode.ALWAYS,  # Strict gate: every call must go through a ToolApprovalBatch.
    concurrency_group="patrol-remediation",
)


class PatrolRemediationTool(BaseTool):
    name = "patrol_remediation"

    def __init__(self, execute_fn: ExecuteRemediationFn, *, session_id: str) -> None:
        super().__init__()
        self._execute_fn = execute_fn
        self._session_id = session_id

    @tool(
        name="patrol_execute_remediation",
        description=(
            "Execute the single proposed Ops Patrol remediation bound to this session, via the "
            "registered Actuator. Requires human approval; the call is queued and never runs before "
            "the operator approves it through the strict HITL gate. Call this at most once per session, "
            "and only after stating the impact of the action to the user."
        ),
        parameters={
            "remediation_id": {"type": "string"},
            "idempotency_key": {"type": "string"},
        },
        required=["remediation_id", "idempotency_key"],
        policy=PATROL_REMEDIATION_POLICY,
    )
    async def patrol_execute_remediation(self, remediation_id: str, idempotency_key: str) -> ToolResult:
        try:
            data = await self._execute_fn(
                remediation_id=remediation_id,
                session_id=self._session_id,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            return ToolResult(success=False, message=str(exc))
        return ToolResult(success=True, data=data, message="Remediation executed")
