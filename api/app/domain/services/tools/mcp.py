"""Domain MCP tool pack backed by an injected connection-pool port."""

from __future__ import annotations

import logging
from typing import Any

from app.domain.external.connection_pool import (
    MCPClientPort,
    MCPConnectionPoolPort,
)
from app.domain.models.integration_runtime import MCPRuntime
from app.domain.models.tool_policy import (
    CONSERVATIVE_TOOL_POLICY,
    ToolDescriptor,
    ToolExecutionPolicy,
)
from app.domain.models.tool_result import ToolResult
from app.domain.runtime_policy import ActivityExecutionPolicy
from app.domain.services.tools.base import BaseTool
from app.domain.services.tools.capability_policy import (
    CapabilityDeniedError,
    CapabilityPolicy,
)
from app.domain.services.tools.errors import ToolInvocationError
from app.domain.services.tools.untrusted import fence_untrusted_tool_result
from app.domain.utils.integration_filter import filter_enabled_mcp_runtime

logger = logging.getLogger(__name__)


class MCPTool(BaseTool):
    """Expose configured MCP tools without owning transport details."""

    name: str = "mcp"

    def __init__(self, connection_pool: MCPConnectionPoolPort) -> None:
        super().__init__()
        self._connection_pool = connection_pool
        self._initialized = False
        self._tools: list[dict[str, Any]] = []
        self._tool_policies: dict[str, ToolExecutionPolicy] = {}
        self._manager: MCPClientPort | None = None
        self._uses_pool = False
        self._init_errors: dict[str, str] = {}

    async def initialize(
        self,
        runtime: MCPRuntime | None = None,
        *,
        policy: ActivityExecutionPolicy,
    ) -> None:
        if self._initialized:
            return
        filtered = filter_enabled_mcp_runtime(runtime) if runtime else MCPRuntime()
        try:
            self._manager = await self._connection_pool.acquire(filtered, policy=policy)
            self._uses_pool = True
            self._tools = await self._manager.get_all_tools()
            self._tool_policies = {
                schema["function"]["name"]: self._manager.get_tool_policy(
                    schema["function"]["name"]
                )
                for schema in self._tools
            }
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("MCP 工具包初始化失败: %s", exc)
            self._init_errors["__init__"] = str(exc)
            self._manager = None
            self._tools = []
            self._tool_policies = {}
            self._uses_pool = False
        self._initialized = True

    def get_tools(self) -> list[dict[str, Any]]:
        if self._capability_policy is None:
            return self._tools
        return self.schemas_for(self._capability_policy)

    def schemas_for(self, policy: CapabilityPolicy) -> list[dict[str, Any]]:
        return [
            schema
            for schema in self._tools
            if policy.allows_integration(
                self._tool_policies.get(
                    schema["function"]["name"],
                    CONSERVATIVE_TOOL_POLICY,
                ),
                tool_name=schema["function"]["name"],
            )
        ]

    def get_tool_descriptor(self, name: str) -> ToolDescriptor:
        schema = next(
            (item for item in self._tools if item.get("function", {}).get("name") == name),
            None,
        )
        if schema is None:
            raise ToolInvocationError(f"工具[{name}]未找到", kind="not_found")
        return ToolDescriptor(
            name=name,
            schema=schema,
            method=self.invoke,
            tool_pack=self.name,
            policy=self._tool_policies.get(name, CONSERVATIVE_TOOL_POLICY),
        )

    def get_tool_descriptors(self) -> list[ToolDescriptor]:
        return [self.get_tool_descriptor(schema["function"]["name"]) for schema in self.get_tools()]

    @property
    def connection_errors(self) -> dict[str, str]:
        errors = dict(self._init_errors)
        if self._manager is not None:
            errors.update(self._manager.connection_errors)
        return errors

    def has_tool(self, tool_name: str) -> bool:
        return any(tool["function"]["name"] == tool_name for tool in self._tools)

    async def invoke(self, tool_name: str, **kwargs: Any) -> ToolResult:
        descriptor = self.get_tool_descriptor(tool_name)
        if self._capability_policy is not None and not self._capability_policy.allows_integration(
            descriptor.policy,
            tool_name=tool_name,
        ):
            raise CapabilityDeniedError(
                f"当前会话策略禁止工具[{tool_name}]",
                layer="execution",
                tool_name=tool_name,
            )
        if self._manager is None:
            return ToolResult(success=False, message="MCP工具未初始化")
        result = await self._manager.invoke(tool_name, kwargs)
        await self._report_transport_health(result)
        # 信任边界（D11）：远端返回内容（含 structuredContent）在进入模型
        # 上下文前统一包裹；巡检等机器通道直接使用底层 manager，不受影响。
        return fence_untrusted_tool_result(result)

    async def _report_transport_health(self, result: ToolResult) -> None:
        report = getattr(self._connection_pool, "report_result", None)
        if report is None or self._manager is None or not self._uses_pool:
            return
        await report(self._manager, success=result.failure_kind != "transport")

    async def cleanup(self) -> None:
        if not self._uses_pool and self._manager is not None:
            await self._manager.cleanup()
        self._tools = []
        self._tool_policies = {}
        self._manager = None
        self._initialized = False
        self._uses_pool = False


__all__ = ["MCPTool"]
