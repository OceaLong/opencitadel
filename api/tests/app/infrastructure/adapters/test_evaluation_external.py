"""Protocol receipts, rather than JSON-RPC transport success, govern effects."""

import json
from uuid import uuid4

import httpx
import pytest

from app.domain.evaluation.environment import TestTarget as Target
from app.domain.execution.tool_contract import RecordedContract
from app.domain.services.tools.capability_policy import EXTERNAL_WRITE
from app.infrastructure.adapters.evaluation_external import RegisteredHTTPTestExecutor


async def invoke_receipt(kind, receipt):
    name = "mcp_write" if kind == "mcp" else "a2a_write"
    contract = RecordedContract(
        name=name,
        pack=kind,
        schema_body={
            "type": "function",
            "function": {"name": name, "parameters": {"type": "object"}},
        },
        policy=EXTERNAL_WRITE,
        connector_id="owned-connector",
        source_name="write",
        binding_revision="fixed-v1",
        authority_revision="fixed-v1",
    )
    target = Target(
        id=uuid4(),
        physical_resource="owned-fixture",
        kind=kind,
        endpoint="https://owned.example.test",
        protocol="mcp-stateless-json-2025-03-26" if kind == "mcp" else "a2a-jsonrpc-0.3",
        connector_id="owned-connector",
        connector_revision="fixed-v1",
        contracts=(contract,),
        allowed_agent_ids=("owned-agent",),
    )
    methods = []

    def respond(request):
        body = json.loads(request.content)
        methods.append(body["method"])
        if body["method"] == "notifications/initialized":
            return httpx.Response(204)
        result = (
            {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}
            if body["method"] == "initialize"
            else receipt
        )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    executor = RegisteredHTTPTestExecutor(target, transport=httpx.MockTransport(respond))
    result = await executor.invoke(
        target, (), None, None, name, {"id": "owned-agent", "query": "perform operation"}
    )
    assert methods == (
        ["initialize", "notifications/initialized", "tools/call"]
        if kind == "mcp"
        else ["message/send"]
    )
    assert result["data"] == receipt
    return result


@pytest.mark.asyncio
async def test_mcp_tool_error_is_not_confirmed_applied():
    result = await invoke_receipt(
        "mcp", {"isError": True, "content": [{"type": "text", "text": "operation failed"}]}
    )
    assert result["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("is_error", [None, False])
async def test_mcp_completed_result_remains_successful(is_error):
    receipt = {"content": [{"type": "text", "text": "operation completed"}]}
    if is_error is not None:
        receipt["isError"] = is_error
    assert (await invoke_receipt("mcp", receipt))["success"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipt",
    [
        {},
        {"content": None},
        {"content": {}},
        {"content": "completed"},
        {"content": [{}]},
        {"content": [{"type": "unknown", "text": "completed"}]},
        {"content": [{"type": "text"}]},
        {"content": [{"type": "text", "text": 1}]},
        {"content": [{"type": "image", "data": "AAAA"}]},
        {"content": [{"type": "audio", "mimeType": "audio/wav"}]},
        {"content": [{"type": "resource", "resource": {"text": "completed"}}]},
        {"content": [{"type": "resource_link", "uri": "file:///result"}]},
        {"content": [], "structuredContent": []},
        {"content": [], "structuredContent": "completed"},
        {"structuredContent": {"completed": True}},
        *[{"content": [], "isError": value} for value in (None, 0, 1, "false", "true")],
    ],
)
async def test_mcp_missing_or_malformed_completion_evidence_is_not_applied(receipt):
    assert (await invoke_receipt("mcp", receipt))["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipt",
    [
        {"content": []},
        {"content": [], "structuredContent": {"completed": True}},
        {
            "content": [{"type": "text", "text": "completed"}],
            "structuredContent": {"completed": True},
        },
        {"content": [{"type": "image", "data": "AAAA", "mimeType": "image/png"}]},
        {"content": [{"type": "audio", "data": "AAAA", "mimeType": "audio/wav"}]},
        {
            "content": [
                {"type": "resource", "resource": {"uri": "file:///result", "text": "completed"}}
            ]
        },
        {"content": [{"type": "resource", "resource": {"uri": "file:///result", "blob": "AAAA"}}]},
        {"content": [{"type": "resource_link", "name": "result", "uri": "file:///result"}]},
    ],
)
async def test_mcp_valid_typed_and_structured_content_remains_successful(receipt):
    assert (await invoke_receipt("mcp", receipt))["success"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    ["submitted", "working", "input-required", "auth-required", "failed", "canceled", "rejected"],
)
async def test_a2a_task_without_successful_completion_is_not_confirmed_applied(state):
    result = await invoke_receipt(
        "a2a", {"kind": "task", "id": "owned-task", "status": {"state": state}}
    )
    assert result["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipt",
    [
        {"kind": "task", "id": "owned-task", "status": {"state": "completed"}},
        {"status": {"state": "completed"}, "artifacts": []},
        {
            "kind": "message",
            "messageId": "owned-message",
            "role": "agent",
            "parts": [{"kind": "text", "text": "operation completed"}],
        },
    ],
)
async def test_a2a_completed_task_or_direct_message_remains_successful(receipt):
    assert (await invoke_receipt("a2a", receipt))["success"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [{}, {"status": {}}, {"status": None}])
async def test_a2a_missing_completion_evidence_is_not_confirmed_applied(receipt):
    assert (await invoke_receipt("a2a", receipt))["success"] is False


@pytest.mark.asyncio
async def test_a2a_invalid_result_object_is_rejected():
    with pytest.raises(ValueError, match="a2a_response_invalid"):
        await invoke_receipt("a2a", [])
