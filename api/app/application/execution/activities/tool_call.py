"""Durable execution of one previously persisted and governed tool call."""

import json

from pydantic_core import to_jsonable_python

from app.application.execution import activity_types
from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.tool_catalog import ExecutionToolCatalog
from app.domain.execution.activity import (
    ActivityContext,
    ActivityOutcome,
    ActivityRequest,
)
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.capability_policy import CapabilityDeniedError
from app.domain.services.tools.errors import ToolInvocationError


class ToolCallActivityHandler:
    activity_type = activity_types.TOOL_CALL
    # Tool policies vary. The kernel treats a recovered in-flight call as unknown
    # instead of guessing whether an external effect happened.
    idempotent = False

    def __init__(
        self,
        *,
        objects: ActivityObjectStore,
        tools: ExecutionToolCatalog,
    ) -> None:
        self._objects = objects
        self._tools = tools

    async def execute(
        self,
        request: ActivityRequest,
        context: ActivityContext,
    ) -> ActivityOutcome:
        if request.input_ref is None:
            return ActivityOutcome.failed(failure_code="ACTIVITY_INPUT_MISSING")
        payload = await self._objects.load_input(
            key=request.input_ref,
            expected_digest=request.input_digest,
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
        # 工具执行契约 v2（D8）：工具级异常（坏参数、能力拒绝、目录漂移、
        # 工具内部失败）归一化为失败的 tool result，作为成功的 activity
        # outcome 喂回模型循环，模型可纠错重试；只有基础设施异常
        # （连接/超时/取消）才继续按 activity 失败击穿。
        try:
            result = await self._tools.invoke(
                payload,
                context,
                name=name,
                arguments=arguments,
                expected_fingerprint=expected_fingerprint,
                approval_feedback=approval_feedback,
            )
        except ToolInvocationError as exc:
            result = _failed_tool_result(str(exc), failure_kind=exc.kind)
        except CapabilityDeniedError as exc:
            result = _failed_tool_result(str(exc), failure_kind="capability_denied")
        content = json.dumps(result, ensure_ascii=False, sort_keys=True)
        result_ref = await self._objects.put_result(
            request.activity_id,
            {
                "kind": "tool",
                "message": {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": content,
                },
            },
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
