"""Tool execution contract v2 (D8): tool-level failures feed the model loop.

坏参数/能力拒绝/目录漂移 → 成功的 activity outcome，result 是失败的 tool
result（模型可纠错重试）；基础设施异常继续按 activity 失败击穿。
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic_core import to_jsonable_python

from app.application.execution.activities.tool_call import ToolCallActivityHandler
from app.domain.execution.activity import ActivityContext, ActivityRequest
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, PolicyBoundTool, tool
from app.domain.services.tools.capability_policy import READ_SAFE
from app.domain.services.tools.errors import ToolInvocationError
from tests.app.execution_test_support import run_execution_context_for

CONTEXT = ActivityContext(
    worker_id="worker-1",
    claim_generation=1,
    idempotency_key="activity-1",
    owner_user_id="user-1",
    team_id=None,
    run=run_execution_context_for("agent"),
)


class _ReportPack(BaseTool):
    name = "report"

    @tool(
        name="write_report",
        description="write a report",
        parameters={
            "filepath": {"type": "string", "description": "path"},
            "lines": {"type": "integer", "description": "line count"},
        },
        required=["filepath"],
        policy=READ_SAFE,
    )
    async def write_report(self, filepath: str, lines: int | None = None) -> ToolResult:
        return ToolResult(success=True, data={"filepath": filepath, "lines": lines})


class _SandboxDownPack(BaseTool):
    name = "sandbox"

    @tool(
        name="shell_probe",
        description="probe",
        parameters={},
        required=[],
        policy=READ_SAFE,
    )
    async def shell_probe(self) -> ToolResult:
        raise OSError("sandbox unreachable")


class _PackCatalog:
    """Minimal ExecutionToolCatalog over one real BaseTool pack."""

    def __init__(self, pack: BaseTool) -> None:
        self._pack = pack

    async def invoke(
        self,
        payload,
        context,
        *,
        name,
        arguments,
        expected_fingerprint=None,
        approval_feedback=None,
    ):
        if not self._pack.has_tool(name):
            raise ToolInvocationError(f"工具[{name}]已不可用", kind="not_found")
        result = await self._pack.invoke(name, **arguments)
        encoded = to_jsonable_python(result)
        assert isinstance(encoded, dict)
        return encoded


class _Objects:
    def __init__(self) -> None:
        self.written = []

    async def load_input(self, *, key, expected_digest):
        return {"session_id": "session-1", "mode": "agent"}

    async def put_result(self, activity_id, payload):
        self.written.append((activity_id, payload))
        return f"result://{activity_id}"


def _request(*, name: str, arguments: dict) -> ActivityRequest:
    return ActivityRequest(
        activity_id=UUID("70000000-0000-0000-0000-000000000001"),
        activity_type="tool.call",
        aggregate_type="run",
        aggregate_id="80000000-0000-0000-0000-000000000001",
        generation=0,
        timeout_at=datetime(2026, 9, 3, tzinfo=UTC),
        input_ref="input://base",
        input_digest="a" * 64,
        input_payload={
            "round": 0,
            "tool_call": {"call_id": "call-1", "name": name, "arguments": arguments},
        },
    )


def _handler(pack: BaseTool) -> tuple[ToolCallActivityHandler, _Objects]:
    objects = _Objects()
    return ToolCallActivityHandler(objects=objects, tools=_PackCatalog(pack)), objects


async def _assert_tool_error_outcome(handler, objects, request, *, expected_text: str):
    outcome = await handler.execute(request, CONTEXT)

    # Activity 成功结算 → agent planner 视为 settled=succeeded，Run 继续。
    assert outcome.status == "succeeded"
    assert outcome.public_data["status"] == "failed"
    _, payload = objects.written[0]
    message = payload["message"]
    assert message["role"] == "tool"
    content = json.loads(message["content"])
    assert content["success"] is False
    assert expected_text in (content["message"] or "")
    return content


async def test_missing_required_argument_feeds_error_back_to_model():
    handler, objects = _handler(_ReportPack())

    content = await _assert_tool_error_outcome(
        handler,
        objects,
        _request(name="write_report", arguments={}),
        expected_text="缺少必填参数",
    )
    assert content["failure_kind"] == "invalid_arguments"


async def test_wrong_argument_type_feeds_error_back_to_model():
    handler, objects = _handler(_ReportPack())

    content = await _assert_tool_error_outcome(
        handler,
        objects,
        _request(
            name="write_report",
            arguments={"filepath": "/tmp/r.md", "lines": "not-a-number"},
        ),
        expected_text="类型不符",
    )
    assert content["failure_kind"] == "invalid_arguments"


async def test_unknown_tool_feeds_not_found_error_back_to_model():
    handler, objects = _handler(_ReportPack())

    content = await _assert_tool_error_outcome(
        handler,
        objects,
        _request(name="vanished_tool", arguments={}),
        expected_text="不可用",
    )
    assert content["failure_kind"] == "not_found"


async def test_infrastructure_exception_keeps_activity_failure_semantics():
    handler, _ = _handler(_SandboxDownPack())

    with pytest.raises(OSError, match="sandbox unreachable"):
        await handler.execute(_request(name="shell_probe", arguments={}), CONTEXT)


async def test_extra_hallucinated_arguments_are_dropped_not_fatal():
    handler, objects = _handler(_ReportPack())

    outcome = await handler.execute(
        _request(
            name="write_report",
            arguments={"filepath": "/tmp/r.md", "hallucinated": True},
        ),
        CONTEXT,
    )

    assert outcome.status == "succeeded"
    assert outcome.public_data["status"] == "completed"
    _, payload = objects.written[0]
    content = json.loads(payload["message"]["content"])
    assert content["data"] == {"filepath": "/tmp/r.md", "lines": None}


class _HangingPack(BaseTool):
    name = "hanging"

    def __init__(self) -> None:
        super().__init__()
        self.cancelled = False

    @tool(
        name="hang_forever",
        description="hang",
        parameters={},
        required=[],
        policy=READ_SAFE,
    )
    async def hang_forever(self) -> ToolResult:
        raise asyncio.CancelledError

    async def on_cancel(self) -> None:
        self.cancelled = True


async def test_policy_bound_tool_forwards_on_cancel_to_wrapped_pack():
    from app.domain.models.session_mode import SessionMode
    from app.domain.services.tools.capability_policy import CapabilityPolicy

    pack = _HangingPack()
    bound = PolicyBoundTool(pack, CapabilityPolicy.for_mode(SessionMode.AGENT))

    await bound.on_cancel()

    assert pack.cancelled is True
