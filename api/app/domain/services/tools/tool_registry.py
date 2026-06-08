#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Central registry for agent tools."""
from typing import Dict, List, Optional

from app.domain.external.browser import Browser
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.base import BaseTool
from app.domain.services.tools.browser import BrowserTool
from app.domain.services.tools.file import FileTool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.message import MessageTool
from app.domain.services.tools.search import SearchTool
from app.domain.services.tools.shell import ShellTool
from app.domain.services.tools.vision import VisionTool


class ToolRegistry:
    """Assemble the default tool packs for Planner/ReAct agents."""

    @staticmethod
    def build_default_tools(
            *,
            sandbox: Sandbox,
            browser: Browser,
            search_engine: SearchEngine,
            llm: LLM,
            mcp_tool: MCPTool,
            a2a_tool: A2ATool,
            extra_tools: Optional[List[BaseTool]] = None,
    ) -> List[BaseTool]:
        tools: List[BaseTool] = [
            FileTool(sandbox=sandbox),
            ShellTool(sandbox=sandbox),
            BrowserTool(browser=browser),
            SearchTool(search_engine=search_engine),
            MessageTool(),
            VisionTool(sandbox=sandbox, llm=llm),
            mcp_tool,
            a2a_tool,
        ]
        if extra_tools:
            tools.extend(extra_tools)
        return tools

    @staticmethod
    def collect_schemas(tools: List[BaseTool]) -> List[Dict]:
        schemas: List[Dict] = []
        for tool in tools:
            schemas.extend(tool.get_tools())
        return schemas
