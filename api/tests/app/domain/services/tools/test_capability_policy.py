from unittest.mock import MagicMock

import pytest

from app.domain.models.codebase import SessionMode
from app.domain.models.app_config import MCPServerConfig
from app.domain.models.tool_policy import (
    ApprovalMode,
    CONSERVATIVE_TOOL_POLICY,
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
INTEGRATION_READ = READ_SAFE.model_copy(
    update={"capability": ToolCapability.INTEGRATION_READ}
)


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
        descriptor.name
        for candidate in tools
        for descriptor in candidate.get_tool_descriptors()
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


def test_ask_child_policy_cannot_expand_parent_policy():
    parent = CapabilityPolicy.for_mode(SessionMode.ASK)

    with pytest.raises(CapabilityDeniedError) as exc_info:
        parent.for_child(requested_tool_names=["shell_execute"])

    # Ask sub-agents with no explicit allowlist yet: denial happens while
    # assembling the child's capability policy, before any tool is exposed.
    assert exc_info.value.layer == "assembly"


def test_unknown_mcp_function_is_hidden_from_ask():
    mcp_tool = MCPTool(MagicMock())
    mcp_tool.register_schema("create_ticket", schema={}, policy=None)
    ToolRegistry.build_tools(
        policy=CapabilityPolicy.for_mode(SessionMode.ASK),
        candidate_tools=[mcp_tool],
    )

    assert mcp_tool.schemas_for(CapabilityPolicy.for_mode(SessionMode.ASK)) == []


def test_admin_classified_read_only_mcp_function_is_visible_in_ask():
    mcp_tool = MCPTool(MagicMock())
    mcp_tool.register_schema(
        "lookup_ticket",
        schema={},
        policy=INTEGRATION_READ,
    )

    assert [
        schema["function"]["name"]
        for schema in mcp_tool.schemas_for(CapabilityPolicy.for_mode(SessionMode.ASK))
    ] == ["lookup_ticket"]


@pytest.mark.parametrize(
    "capability",
    [
        ToolCapability.MESSAGE,
        ToolCapability.KNOWLEDGE_READ,
        ToolCapability.CODE_READ,
    ],
)
def test_integration_read_declaration_with_non_integration_capability_is_hidden_from_ask(
        capability,
):
    mcp_tool = MCPTool(MagicMock())
    mcp_tool.register_schema(
        "lookup_ticket",
        schema={},
        policy=READ_SAFE.model_copy(update={"capability": capability}),
    )
    tools = ToolRegistry.build_tools(
        policy=CapabilityPolicy.for_mode(SessionMode.ASK),
        candidate_tools=[mcp_tool],
    )

    assert mcp_tool.schemas_for(CapabilityPolicy.for_mode(SessionMode.ASK)) == []
    assert ToolRegistry.collect_schemas(tools) == []


def test_mcp_server_config_parses_typed_tool_policies():
    config = MCPServerConfig(
        url="https://mcp.example.test",
        tool_policies={"lookup_ticket": INTEGRATION_READ.model_dump(mode="json")},
    )

    assert config.tool_policies["lookup_ticket"] == INTEGRATION_READ


def test_mcp_server_config_normalizes_mislabeled_read_to_conservative():
    config = MCPServerConfig(
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


def test_agent_child_policy_cannot_expand_beyond_allowlist():
    parent = CapabilityPolicy.for_mode(
        SessionMode.AGENT,
        allowed_tool_names=["kb_search"],
    )

    with pytest.raises(CapabilityDeniedError) as exc_info:
        parent.for_child(requested_tool_names=["shell_execute"])

    # Parent already has an explicit allowlist: denial happens because the
    # child requested a tool that would be *exposed* beyond what the parent
    # granted, distinct from the "no allowlist at all" assembly case above.
    assert exc_info.value.layer == "exposure"


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
