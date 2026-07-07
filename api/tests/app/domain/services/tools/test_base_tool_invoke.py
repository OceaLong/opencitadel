#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.models.tool_result import ToolResult, normalize_tool_result
from app.domain.services.tools.base import BaseTool, tool


class _StringTool(BaseTool):
    name: str = "string_tool"

    @tool(
        name="echo",
        description="Return a plain string",
        parameters={"text": {"type": "string", "description": "Text to echo"}},
        required=["text"],
    )
    async def echo(self, text: str) -> str:
        return text


class _StructuredTool(BaseTool):
    name: str = "structured_tool"

    @tool(
        name="run",
        description="Return a ToolResult",
        parameters={},
        required=[],
    )
    async def run(self) -> ToolResult:
        return ToolResult(success=False, message="failed", data=None)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_normalize_tool_result_wraps_string():
    result = normalize_tool_result("hello")
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.data == "hello"


def test_normalize_tool_result_passthrough():
    original = ToolResult(success=False, message="err", data={"k": 1})
    assert normalize_tool_result(original) is original


@pytest.mark.anyio
async def test_invoke_wraps_string_return():
    tool = _StringTool()
    result = await tool.invoke("echo", text="tree")
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.data == "tree"


@pytest.mark.anyio
async def test_invoke_passthrough_tool_result():
    tool = _StructuredTool()
    result = await tool.invoke("run")
    assert result.success is False
    assert result.message == "failed"
