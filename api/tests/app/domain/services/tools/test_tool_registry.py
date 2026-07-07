#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import MagicMock

from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.base import BaseTool
from app.domain.services.tools.browser import BrowserTool
from app.domain.services.tools.file import FileTool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.message import MessageTool
from app.domain.services.tools.shell import ShellTool
from app.domain.services.tools.tool_registry import ToolRegistry


class _DummyExtraTool(BaseTool):
    name: str = "dummy"


def test_build_ask_tools_excludes_shell_file_browser():
    mcp_tool = MagicMock(spec=MCPTool)
    a2a_tool = MagicMock(spec=A2ATool)
    extra = _DummyExtraTool()

    tools = ToolRegistry.build_ask_tools(
        mcp_tool=mcp_tool,
        a2a_tool=a2a_tool,
        extra_tools=[extra],
    )

    tool_types = {type(tool) for tool in tools}
    assert FileTool not in tool_types
    assert ShellTool not in tool_types
    assert BrowserTool not in tool_types
    assert MessageTool in tool_types
    assert mcp_tool in tools
    assert a2a_tool in tools
    assert extra in tools
