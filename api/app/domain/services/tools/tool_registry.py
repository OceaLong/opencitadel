"""Central registry for agent tools."""

from app.domain.external.browser import Browser
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.models.session_mode import SessionMode
from app.domain.services import vision_service
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.base import BaseTool, PolicyBoundTool
from app.domain.services.tools.browser import BrowserTool
from app.domain.services.tools.capability_policy import CapabilityPolicy
from app.domain.services.tools.file import FileTool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.search import SearchTool
from app.domain.services.tools.shell import ShellTool
from app.domain.services.tools.vision import VisionTool
from app.domain.services.tools.vision_grounding import VisionGroundingTool


class ToolRegistry:
    """Assemble the default tool packs for Planner/ReAct agents."""

    @staticmethod
    def build_default_tools(
        *,
        sandbox: Sandbox,
        browser: Browser,
        search_engine: SearchEngine | None,
        llm: LLM,
        mcp_tool: MCPTool,
        a2a_tool: A2ATool,
        extra_tools: list[BaseTool] | None = None,
        policy: CapabilityPolicy | None = None,
    ) -> list[BaseTool]:
        tools: list[BaseTool] = [
            FileTool(sandbox=sandbox),
            ShellTool(sandbox=sandbox),
            BrowserTool(browser=browser),
            mcp_tool,
            a2a_tool,
        ]
        # SEARCH_PROVIDER=none 时不注册搜索工具：显式缺席优于静默空结果。
        if search_engine is not None:
            tools.insert(3, SearchTool(search_engine=search_engine))
        if vision_service.vision_enabled(llm):
            tools.extend(
                [
                    VisionTool(sandbox=sandbox, llm=llm),
                    VisionGroundingTool(sandbox=sandbox, llm=llm),
                ]
            )
        if extra_tools:
            tools.extend(extra_tools)
        return ToolRegistry.build_tools(
            policy=policy or CapabilityPolicy.for_mode(SessionMode.AGENT),
            candidate_tools=tools,
        )

    @staticmethod
    def build_ask_tools(
        *,
        mcp_tool: MCPTool,
        a2a_tool: A2ATool,
        extra_tools: list[BaseTool] | None = None,
        policy: CapabilityPolicy | None = None,
    ) -> list[BaseTool]:
        """Assemble read-only tool packs for Ask-mode flows (no shell/file/browser)."""
        tools: list[BaseTool] = [
            mcp_tool,
            a2a_tool,
        ]
        if extra_tools:
            tools.extend(extra_tools)
        return ToolRegistry.build_tools(
            policy=policy or CapabilityPolicy.for_mode(SessionMode.ASK),
            candidate_tools=tools,
        )

    @staticmethod
    def collect_schemas(tools: list[BaseTool]) -> list[dict]:
        schemas: list[dict] = []
        for tool in tools:
            schemas.extend(tool.get_tools())
        return schemas

    @staticmethod
    def build_tools(
        *,
        policy: CapabilityPolicy,
        candidate_tools: list[BaseTool],
    ) -> list[BaseTool]:
        return [
            PolicyBoundTool(candidate, policy) if isinstance(candidate, BaseTool) else candidate
            for candidate in candidate_tools
        ]
