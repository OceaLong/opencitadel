"""Refresh a selected intent without invoking a tool or mutating prior approval."""

from app.application.execution import activity_types
from app.application.execution.invocation_contracts import freeze_calls
from app.domain.evaluation.recording import validate_typed
from app.domain.execution.activity import ActivityOutcome
from app.domain.execution.tool_contract import ToolApprovalSubject, ToolContractChanged


class ToolIntentRefreshActivityHandler:
    activity_type = activity_types.TOOL_INTENT_REFRESH
    idempotent = True

    def __init__(self, *, objects, tools):
        self.objects, self.tools = objects, tools

    async def execute(self, request, context):
        original = request.input_payload["tool_call"]
        previous = ToolApprovalSubject.model_validate(request.input_payload["previous_subject"])
        try:
            payload = await self.objects.load_input(
                key=request.input_ref, expected_digest=request.input_digest
            )
            snapshot = await self.tools.definitions(payload, context)
            definition = next((d for d in snapshot.definitions if d.name == original["name"]), None)
            if definition is None or definition.contract is None:
                raise ToolContractChanged()
            validate_typed(original["arguments"], definition.contract.arguments_schema)
            call = {
                "call_id": original["call_id"],
                "name": original["name"],
                "arguments": dict(original["arguments"]),
                "requires_approval": True,
                "approval_kind": "tool_effect",
                "risk_summary": definition.risk_summary,
            }
            await freeze_calls(self.objects, request, snapshot, [call])
            # Already confirmed feedback remains ordinary canonical arguments.
            # Renewal always gets one new exact approval, never another choice gate.
            call.pop("approval_feedback_param", None)
            call["pure_clarification"] = False
            if call["subject"]["invocation_id"] == str(previous.invocation_id):
                raise ValueError("renewal must create a distinct invocation")
            result = {"kind": "tool_intent_refresh", "tool_call": call}
            result_ref = await self.objects.put_result(
                request.activity_id, result, run_id=context.run.run_id
            )
            return ActivityOutcome.succeeded(
                result_ref=result_ref,
                result_summary="Tool intent refreshed; new approval required",
                decision_data=result,
                public_data={
                    "kind": "tool",
                    "name": original["name"],
                    "status": "awaiting_approval",
                },
            )
        except (ToolContractChanged, PermissionError, ValueError, OSError, RuntimeError) as error:
            reason = (
                "authorization_changed"
                if isinstance(error, PermissionError)
                or (
                    isinstance(error, ToolContractChanged)
                    and error.reason.startswith("authorization")
                )
                else "contract_changed"
            )
            return ActivityOutcome(
                status="failed",
                failure_code="TOOL_INTENT_REFRESH_FAILED",
                public_data={
                    "status": "not_sent",
                    "contract_drift": {
                        "reason": reason,
                        "tool_name": original["name"],
                        "subject_digest": previous.digest,
                    },
                },
            )
