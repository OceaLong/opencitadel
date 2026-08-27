import pytest
from pydantic import ValidationError

from app.domain.models.integration_runtime import (
    A2ARuntime,
    MCPRuntime,
    MCPTransport,
)
from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.domain.utils.integration_runtime_builder import (
    a2a_records_to_runtime,
    mcp_records_to_runtime,
)

INTEGRATION_READ = ToolExecutionPolicy(
    capability=ToolCapability.INTEGRATION_READ,
    effect=ToolEffect.READ_ONLY,
    idempotency=ToolIdempotency.SAFE,
    approval=ApprovalMode.NEVER,
)


def test_integration_runtime_builders_preserve_ids_and_typed_tool_policies():
    mcp = MCPServerRecord(
        id="mcp-1",
        name="tickets",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="https://mcp.example.test",
        tool_policies={"lookup_ticket": INTEGRATION_READ},
    )
    a2a = A2AServerRecord(
        id="a2a-1",
        base_url="https://agent.example.test",
        tool_policies={"get_remote_agent_cards": INTEGRATION_READ},
    )

    mcp_runtime = mcp_records_to_runtime([mcp])
    a2a_runtime = a2a_records_to_runtime([a2a])

    assert mcp_runtime.servers["mcp-1"].name == "tickets"
    assert mcp_runtime.servers["mcp-1"].tool_policies == mcp.tool_policies
    assert a2a_runtime.servers[0].id == "a2a-1"
    assert a2a_runtime.servers[0].tool_policies == a2a.tool_policies


def test_integration_runtime_is_closed_and_keys_servers_by_stable_id():
    runtime = MCPRuntime.model_validate(
        {
            "servers": {
                "server-1": {
                    "id": "server-1",
                    "name": "docs",
                    "transport": "streamable_http",
                    "url": "https://mcp.example.test",
                    "enabled": True,
                    "transport_options": {},
                }
            }
        }
    )

    assert runtime.servers["server-1"].transport is MCPTransport.STREAMABLE_HTTP
    with pytest.raises(ValidationError):
        MCPRuntime.model_validate({"servers": {}, "legacy_config": {}})
    with pytest.raises(ValidationError):
        MCPRuntime.model_validate(
            {
                "servers": {
                    "wrong-key": {
                        "id": "server-1",
                        "name": "docs",
                        "transport": "streamable_http",
                        "url": "https://mcp.example.test",
                    }
                }
            }
        )


def test_a2a_runtime_is_closed_and_immutable():
    runtime = A2ARuntime.model_validate(
        {
            "servers": [
                {
                    "id": "a2a-1",
                    "base_url": "https://agent.example.test",
                }
            ]
        }
    )

    assert runtime.servers[0].id == "a2a-1"
    with pytest.raises(ValidationError):
        A2ARuntime.model_validate({"servers": [], "a2a_servers": []})


def test_mcp_runtime_rejects_duplicate_display_names():
    first = MCPServerRecord(id="server-1", name="docs", url="https://one.example.test")
    second = MCPServerRecord(id="server-2", name="docs", url="https://two.example.test")

    with pytest.raises(ValidationError, match="names must be unique"):
        mcp_records_to_runtime([first, second])
