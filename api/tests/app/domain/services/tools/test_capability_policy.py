from unittest.mock import MagicMock

import pytest

from app.domain.models.integration_runtime import MCPServerRuntime
from app.domain.models.session_mode import SessionMode
from app.domain.models.tool_policy import (
    CONSERVATIVE_TOOL_POLICY,
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.capability_policy import (
    CapabilityDeniedError,
    CapabilityPolicy,
)
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.tool_registry import ToolRegistry

READ_SAFE = ToolExecutionPolicy(
    capability=ToolCapability.KNOWLEDGE_READ,
    effect=ToolEffect.READ_ONLY,
    idempotency=ToolIdempotency.SAFE,
    approval=ApprovalMode.NEVER,
)
WEB_READ = READ_SAFE.model_copy(update={"capability": ToolCapability.WEB_READ})
WRITE = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.WORKSPACE_WRITE,
    idempotency=ToolIdempotency.NON_IDEMPOTENT,
    approval=ApprovalMode.POLICY,
    concurrency_group="filesystem",
)
INTEGRATION_READ = READ_SAFE.model_copy(update={"capability": ToolCapability.INTEGRATION_READ})


def _seed_mcp_schema(mcp_tool: MCPTool, name: str, *, policy) -> None:
    """Simulate a remote MCP tool schema directly on the pack caches."""
    mcp_tool._tools.append(
        {
            "type": "function",
            "function": {"name": name, "description": "", "parameters": {}},
        }
    )
    mcp_tool._tool_policies[name] = policy or CONSERVATIVE_TOOL_POLICY


class _MixedTool(BaseTool):
    name = "mixed"

    @tool(name="kb_search", description="read", parameters={}, required=[], policy=READ_SAFE)
    async def read(self):
        return {"value": "safe"}

    @tool(name="search_web", description="web", parameters={}, required=[], policy=WEB_READ)
    async def web(self):
        return {"value": "web"}

    @tool(name="write_file", description="write", parameters={}, required=[], policy=WRITE)
    async def write(self):
        return {"value": "written"}


def test_ask_exposes_only_explicit_ask_safe_descriptors():
    tools = ToolRegistry.build_tools(
        policy=CapabilityPolicy.for_mode(SessionMode.ASK),
        candidate_tools=[_MixedTool()],
    )

    names = {
        descriptor.name for candidate in tools for descriptor in candidate.get_tool_descriptors()
    }

    assert names == {"kb_search"}


@pytest.mark.asyncio
async def test_ask_denies_direct_invocation_of_filtered_write_tool():
    tool_pack = ToolRegistry.build_tools(
        policy=CapabilityPolicy.for_mode(SessionMode.ASK),
        candidate_tools=[_MixedTool()],
    )[0]

    with pytest.raises(CapabilityDeniedError) as exc_info:
        await tool_pack.invoke("write_file")

    # BaseTool.invoke's denial (tools/base.py) is an execution-layer
    # rejection — the request cleared assembly/exposure and was denied only
    # when actually invoked.
    assert exc_info.value.layer == "execution"
    assert exc_info.value.tool_name == "write_file"


def test_unknown_mcp_function_is_hidden_from_ask():
    mcp_tool = MCPTool(MagicMock())
    _seed_mcp_schema(mcp_tool, "create_ticket", policy=None)
    ToolRegistry.build_tools(
        policy=CapabilityPolicy.for_mode(SessionMode.ASK),
        candidate_tools=[mcp_tool],
    )

    assert mcp_tool.schemas_for(CapabilityPolicy.for_mode(SessionMode.ASK)) == []


def test_admin_classified_read_only_mcp_function_is_visible_in_ask():
    mcp_tool = MCPTool(MagicMock())
    _seed_mcp_schema(mcp_tool, "lookup_ticket", policy=INTEGRATION_READ)

    assert [
        schema["function"]["name"]
        for schema in mcp_tool.schemas_for(CapabilityPolicy.for_mode(SessionMode.ASK))
    ] == ["lookup_ticket"]


@pytest.mark.parametrize(
    "capability",
    [
        ToolCapability.KNOWLEDGE_READ,
        ToolCapability.CODE_READ,
    ],
)
def test_integration_read_declaration_with_non_integration_capability_is_hidden_from_ask(
    capability,
):
    mcp_tool = MCPTool(MagicMock())
    _seed_mcp_schema(
        mcp_tool,
        "lookup_ticket",
        policy=READ_SAFE.model_copy(update={"capability": capability}),
    )
    tools = ToolRegistry.build_tools(
        policy=CapabilityPolicy.for_mode(SessionMode.ASK),
        candidate_tools=[mcp_tool],
    )

    assert mcp_tool.schemas_for(CapabilityPolicy.for_mode(SessionMode.ASK)) == []
    assert ToolRegistry.collect_schemas(tools) == []


def test_mcp_server_runtime_parses_typed_tool_policies():
    config = MCPServerRuntime(
        id="server-1",
        name="tickets",
        url="https://mcp.example.test",
        tool_policies={"lookup_ticket": INTEGRATION_READ.model_dump(mode="json")},
    )

    assert config.tool_policies["lookup_ticket"] == INTEGRATION_READ


def test_mcp_server_runtime_normalizes_mislabeled_read_to_conservative():
    config = MCPServerRuntime(
        id="server-1",
        name="tickets",
        url="https://mcp.example.test",
        tool_policies={"lookup_ticket": READ_SAFE.model_dump(mode="json")},
    )

    assert config.tool_policies["lookup_ticket"] == CONSERVATIVE_TOOL_POLICY


def test_agent_policy_respects_tool_allowlist():
    policy = CapabilityPolicy.for_mode(
        SessionMode.AGENT,
        allowed_tool_names=["kb_search"],
    )

    assert policy.allows(READ_SAFE, tool_name="kb_search")
    assert not policy.allows(WRITE, tool_name="write_file")


@pytest.mark.asyncio
async def test_policy_binding_is_isolated_for_shared_tool_pack():
    shared = _MixedTool()
    parent = ToolRegistry.build_tools(
        policy=CapabilityPolicy.for_mode(SessionMode.AGENT),
        candidate_tools=[shared],
    )[0]
    child = ToolRegistry.build_tools(
        policy=CapabilityPolicy.for_mode(
            SessionMode.AGENT,
            allowed_tool_names=["kb_search"],
        ),
        candidate_tools=[shared],
    )[0]

    parent_result = await parent.invoke("write_file")
    with pytest.raises(CapabilityDeniedError) as exc_info:
        await child.invoke("write_file")

    assert parent_result.success is True
    # PolicyBoundTool.invoke's denial (tools/base.py) is execution-layer too.
    assert exc_info.value.layer == "execution"
    assert exc_info.value.tool_name == "write_file"
    assert {item["function"]["name"] for item in parent.get_tools()} == {
        "kb_search",
        "search_web",
        "write_file",
    }
