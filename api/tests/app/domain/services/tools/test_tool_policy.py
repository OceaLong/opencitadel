from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.domain.services.tools.base import BaseTool, tool


class ReadTool(BaseTool):
    name = "read"

    @tool(
        name="read_value",
        description="read",
        parameters={},
        required=[],
        policy=ToolExecutionPolicy(
            capability=ToolCapability.KNOWLEDGE_READ,
            effect=ToolEffect.READ_ONLY,
            idempotency=ToolIdempotency.SAFE,
            approval=ApprovalMode.NEVER,
        ),
    )
    async def read_value(self):
        return "ok"


def test_tool_descriptor_exposes_execution_policy():
    descriptor = ReadTool().get_tool_descriptor("read_value")

    assert descriptor.policy.effect == ToolEffect.READ_ONLY
    assert descriptor.policy.idempotency == ToolIdempotency.SAFE


def test_missing_policy_is_conservative():
    class UndeclaredPolicyTool(BaseTool):
        name = "undeclared_policy"

        @tool(name="undeclared_call", description="undeclared", parameters={}, required=[])
        async def undeclared_call(self):
            return "ok"

    policy = UndeclaredPolicyTool().get_tool_descriptor("undeclared_call").policy

    assert policy.capability == ToolCapability.UNKNOWN
    assert policy.effect == ToolEffect.INTERACTIVE
    assert policy.idempotency == ToolIdempotency.UNKNOWN
    assert policy.approval == ApprovalMode.ALWAYS
