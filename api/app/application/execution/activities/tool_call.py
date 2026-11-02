"""Durable execution of one previously persisted and governed tool call."""

import json

from pydantic_core import to_jsonable_python

from app.application.execution import activity_types
from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.decisions.context import context_enabled
from app.application.execution.invocation_contracts import load_contract
from app.application.execution.tool_catalog import ExecutionToolCatalog
from app.application.ports.inference_dispatch import auxiliary_activity_context
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.execution.activity import (
    ActivityContext,
    ActivityOutcome,
    ActivityRequest,
)
from app.domain.execution.effect import EffectOperationSpec
from app.domain.execution.tool_contract import (
    ToolApprovalSubject,
    ToolContractChanged,
    ToolPreparationFailed,
)
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.capability_policy import CapabilityDeniedError
from app.domain.services.tools.errors import ToolInvocationError


class ToolCallActivityHandler:
    activity_type = activity_types.TOOL_CALL
    # Recovery is selected per Invocation: proven native safety or a durable
    # journal that can recover without repeating an uncertain transport call.
    idempotent = False

    def __init__(
        self,
        *,
        objects: ActivityObjectStore,
        tools: ExecutionToolCatalog,
        replay=None,
        execution_usage=None,
        effects=None,
    ) -> None:
        self._execution_usage = execution_usage
        self._replay = replay
        self._objects = objects
        self._tools = tools
        self._effects = effects

    async def recovery_safe(self, request, run):
        if self._replay is not None and await self._replay.recovery_safe(request, run):
            return True
        raw_subject = request.input_payload.get("subject")
        if not isinstance(raw_subject, dict):
            return False
        try:
            subject = ToolApprovalSubject.model_validate(raw_subject)
            contract = await load_contract(
                self._objects,
                subject,
                call=request.input_payload["tool_call"],
                request=request,
                bounded=context_enabled(run.definition_manifest),
                read_limit=(
                    run.definition_manifest.context_policy.read_bytes
                    if context_enabled(run.definition_manifest)
                    else 65536
                ),
            )
            return contract.recovery_safe or (
                contract.requires_operation
                and self._effects is not None
                and callable(getattr(self._effects, "recover", None))
            )
        except (ValueError, KeyError, TypeError):
            return False

    async def execute(self, request, context):
        with auxiliary_activity_context(self._execution_usage, request, context):
            return await self._execute(request, context)

    async def _execute(
        self,
        request: ActivityRequest,
        context: ActivityContext,
    ) -> ActivityOutcome:
        if request.input_ref is None:
            return ActivityOutcome.failed(failure_code="ACTIVITY_INPUT_MISSING")
        bounded = context_enabled(context.run.definition_manifest)
        read_limit = context.run.definition_manifest.context_policy.read_bytes if bounded else 65536
        load = self._objects.load_input_bounded if bounded else self._objects.load_input
        payload = await load(
            key=request.input_ref,
            expected_digest=request.input_digest,
            **({"limit": read_limit} if bounded else {}),
        )
        raw_call = request.input_payload.get("tool_call")
        if not isinstance(raw_call, dict):
            return ActivityOutcome.failed(failure_code="TOOL_CALL_INVALID")
        call_id = raw_call.get("call_id")
        name = raw_call.get("name")
        arguments = raw_call.get("arguments")
        if not isinstance(call_id, str) or not call_id.strip():
            return ActivityOutcome.failed(failure_code="TOOL_CALL_INVALID")
        if not isinstance(name, str) or not name.strip():
            return ActivityOutcome.failed(failure_code="TOOL_CALL_INVALID")
        if not isinstance(arguments, dict):
            return ActivityOutcome.failed(failure_code="TOOL_CALL_INVALID")
        expected_fingerprint = request.input_payload.get("catalog_fingerprint")
        if not isinstance(expected_fingerprint, str):
            expected_fingerprint = None
        # The reviewer's approval feedback (e.g. the option picked on a
        # clarification card) rides along for tools that declare a receiving
        # parameter; the catalog routes it data-driven.
        approval_feedback = request.input_payload.get("approval_feedback")
        if not isinstance(approval_feedback, str) or not approval_feedback:
            approval_feedback = None
        # Definite argument errors may be model-correctable. Contract or
        # authorization drift is no-send waiting; a sent write with uncertain
        # outcome belongs to the effect journal and never becomes a tool error.
        subject = None
        try:
            if self._replay is not None and await self._replay.active(context):
                result = await self._replay.tool(request, context)
            else:
                bound = {}
                recovered = None
                raw_subject = request.input_payload.get("subject")
                if isinstance(raw_subject, dict):
                    try:
                        subject = ToolApprovalSubject.model_validate(raw_subject)
                        contract = await load_contract(
                            self._objects,
                            subject,
                            call=raw_call,
                            request=request,
                            bounded=bounded,
                            read_limit=read_limit,
                        )
                    except ToolContractChanged:
                        raise
                    except (ValueError, TypeError, KeyError) as error:
                        raise ToolContractChanged("invocation_binding_invalid") from error
                    bound = {"invocation": subject, "contract": contract}
                    if contract.requires_operation:
                        if self._effects is None:
                            raise ToolContractChanged("effect_service_required")
                        spec = EffectOperationSpec(
                            operation_id=subject.invocation_id,
                            invocation_id=subject.invocation_id,
                            run_id=context.run.run_id,
                            activity_id=request.activity_id,
                            generation=request.generation,
                            provider_identity=contract.pack,
                            provider_locator={
                                "connector_id": contract.connector_id,
                                "tool_name": contract.name,
                                "tool_call_id": subject.tool_call_id,
                            },
                            contract_digest=contract.digest,
                            arguments_digest=subject.arguments_digest,
                            target_digest=subject.target_digest,
                            idempotency_key=f"inv:{subject.invocation_id}",
                            capability=contract.effect_contract.capability,
                            output_schema=contract.output_schema,
                        )

                        async def execute(send, preflight):
                            return await self._effects.invoke(
                                spec, context, send, preflight=preflight
                            )

                        bound["execute"] = execute
                        # A receipt can commit before CompleteActivity. Read it
                        # before rediscovery: later catalog/permission changes
                        # must neither repeat the call nor erase its outcome.
                        recovered = await self._effects.recover(spec, context)
                result = (
                    recovered
                    if recovered is not None
                    else await self._tools.invoke(
                        payload,
                        context,
                        name=name,
                        arguments=arguments,
                        expected_fingerprint=expected_fingerprint,
                        approval_feedback=approval_feedback,
                        **bound,
                    )
                )
                if bound.get("execute") is not None:
                    if result.state != "applied" or result.result is None:
                        return ActivityOutcome.unknown(failure_code="EXTERNAL_OUTCOME_UNKNOWN")
                    result = result.result
        except ToolPreparationFailed:
            return ActivityOutcome.failed(failure_code="TOOL_PREPARATION_FAILED")
        except ToolContractChanged as exc:
            return ActivityOutcome(
                status="failed",
                failure_code="TOOL_AUTHORIZATION_CHANGED"
                if exc.reason.startswith("authorization")
                else "TOOL_CONTRACT_CHANGED",
                public_data={
                    "status": "not_sent",
                    "contract_drift": {
                        "reason": "authorization_changed"
                        if exc.reason.startswith("authorization")
                        else "contract_changed",
                        "tool_name": name,
                        "subject_digest": subject.digest if subject else None,
                    },
                },
            )
        except ReplayMismatch:
            return ActivityOutcome.failed(failure_code=ReplayMismatch.code)
        except ToolInvocationError as exc:
            result = _failed_tool_result(str(exc), failure_kind=exc.kind)
        except CapabilityDeniedError as exc:
            result = _failed_tool_result(str(exc), failure_kind="capability_denied")
        result = dict(result)
        resource_evidence = result.pop("_context_resource_evidence", None)
        content = json.dumps(result, ensure_ascii=False, sort_keys=True)
        result_body = {
            "kind": "tool",
            "message": {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": content,
            },
        }
        if bounded and resource_evidence is not None:
            from app.domain.execution.context_resources import ContextResourceEvidence

            result_body["resource_evidence"] = ContextResourceEvidence.model_validate(
                resource_evidence
            ).model_dump(mode="json")
        definition = context.run.definition_manifest
        indexed_context = definition is not None and any(
            item.activity_type == activity_types.CONTEXT_PREPARE for item in definition.handlers
        )
        result_ref = (
            await self._objects.put_context_result(
                request.activity_id, result_body, run=context.run
            )
            if indexed_context
            else await self._objects.put_result(
                request.activity_id, result_body, run_id=context.run.run_id
            )
        )
        return ActivityOutcome.succeeded(
            result_ref=result_ref,
            result_summary=content[:4096],
            public_data={
                "kind": "tool",
                "tool_call_id": call_id,
                "name": name,
                "arguments": arguments,
                "status": "completed" if result.get("success") is True else "failed",
                "content": content[:16_384],
            },
        )


def _failed_tool_result(message: str, *, failure_kind: str) -> dict:
    """失败的 tool result，与成功路径的 ToolResult 序列化形状同构。"""
    encoded = to_jsonable_python(
        ToolResult(success=False, message=message, failure_kind=failure_kind)
    )
    assert isinstance(encoded, dict)
    return encoded


__all__ = ["ToolCallActivityHandler"]
