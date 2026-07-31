from app.domain.models.app_config import MCPTransport
from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.domain.utils.integration_config_builder import (
    a2a_records_to_config,
    mcp_records_to_config,
)


INTEGRATION_READ = ToolExecutionPolicy(
    capability=ToolCapability.INTEGRATION_READ,
    effect=ToolEffect.READ_ONLY,
    idempotency=ToolIdempotency.SAFE,
    approval=ApprovalMode.NEVER,
)


def test_integration_config_builders_preserve_typed_tool_policies():
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

    mcp_config = mcp_records_to_config([mcp])
    a2a_config = a2a_records_to_config([a2a])

    assert mcp_config.mcpServers["tickets"].tool_policies == mcp.tool_policies
    assert a2a_config.a2a_servers[0].tool_policies == a2a.tool_policies
