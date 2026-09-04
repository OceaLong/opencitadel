"""Policy binding helpers for assembled tool packs.

历史上这里还有 ``build_default_tools``/``build_ask_tools`` 双清单；工具装配
唯一真源已收敛到 ``app.application.execution.agent_tool_catalog`` 的
ToolSpec 装配表（D10），本模块只保留策略绑定与 schema 汇总原语。
"""

from typing import Any

from app.domain.services.tools.base import BaseTool, PolicyBoundTool
from app.domain.services.tools.capability_policy import CapabilityPolicy


class ToolRegistry:
    """Bind capability policies onto candidate tool packs."""

    @staticmethod
    def collect_schemas(tools: list[BaseTool]) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
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
