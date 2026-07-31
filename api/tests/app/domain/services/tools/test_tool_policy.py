#!/usr/bin/env python
# -*- coding: utf-8 -*-
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
            capability=ToolCapability.MESSAGE,
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
    class LegacyTool(BaseTool):
        name = "legacy"

        @tool(name="legacy_call", description="legacy", parameters={}, required=[])
        async def legacy_call(self):
            return "ok"

    policy = LegacyTool().get_tool_descriptor("legacy_call").policy

    assert policy.capability == ToolCapability.UNKNOWN
    assert policy.effect == ToolEffect.INTERACTIVE
    assert policy.idempotency == ToolIdempotency.UNKNOWN
    assert policy.approval == ApprovalMode.ALWAYS
