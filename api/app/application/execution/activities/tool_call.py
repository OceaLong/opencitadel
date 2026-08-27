"""Durable execution of one previously persisted and governed tool call."""

import json

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.tool_catalog import ExecutionToolCatalog
from app.domain.execution.activity import (
    ActivityContext,
    ActivityOutcome,
    ActivityRequest,
)


class ToolCallActivityHandler:
    activity_type = "tool.call"
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
        result = await self._tools.invoke(
            payload,
            context,
            name=name,
            arguments=arguments,
        )
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
                "status": "completed" if result.get("success") is True else "failed",
                "content": content[:16_384],
            },
        )


__all__ = ["ToolCallActivityHandler"]
