"""Domain A2A tool pack backed by an injected connection-pool port."""

from __future__ import annotations

import logging
from typing import Any

from app.domain.external.connection_pool import (
    A2AClientPort,
    A2AConnectionPoolPort,
)
from app.domain.models.integration_runtime import A2ARuntime
from app.domain.models.tool_policy import (
    CONSERVATIVE_TOOL_POLICY,
    ToolDescriptor,
    ToolExecutionPolicy,
)
from app.domain.models.tool_result import ToolResult
from app.domain.runtime_policy import ActivityExecutionPolicy
from app.domain.services.tools.base import BaseTool, tool
from app.domain.utils.integration_filter import filter_enabled_a2a_runtime

logger = logging.getLogger(__name__)


class A2ATool(BaseTool):
    """Expose configured A2A agents without owning transport details."""

    name: str = "a2a"

    def __init__(self, connection_pool: A2AConnectionPoolPort) -> None:
        super().__init__()
        self._connection_pool = connection_pool
        self._initialized = False
        self.manager: A2AClientPort | None = None
        self._uses_pool = False
        self._tool_policies: dict[str, ToolExecutionPolicy] = {}

    @staticmethod
    def _aggregate_policy(
        servers: list[Any],
        function_name: str,
    ) -> ToolExecutionPolicy:
        configured = [
            server.tool_policies.get(function_name) for server in servers if server.enabled
        ]
        if not configured or any(policy is None for policy in configured):
            return CONSERVATIVE_TOOL_POLICY
        first = configured[0]
        if any(policy != first for policy in configured[1:]):
            return CONSERVATIVE_TOOL_POLICY
        return first

    async def initialize(
        self,
        runtime: A2ARuntime | None = None,
        *,
        policy: ActivityExecutionPolicy,
    ) -> None:
        if self._initialized:
            return
        filtered = filter_enabled_a2a_runtime(runtime) if runtime else A2ARuntime()
        self._tool_policies = {
            name: self._aggregate_policy(filtered.servers, name)
            for name in ("get_remote_agent_cards", "call_remote_agent")
        }
        self._tools_cache = None
        try:
            self.manager = await self._connection_pool.acquire(filtered, policy=policy)
            self._uses_pool = True
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("A2A 工具包初始化失败: %s", exc)
            self.manager = None
            self._uses_pool = False
        self._initialized = True

    def get_tool_descriptor(self, name: str) -> ToolDescriptor:
        descriptor = super().get_tool_descriptor(name)
        return ToolDescriptor(
            name=descriptor.name,
            schema=descriptor.schema,
            method=descriptor.method,
            tool_pack=descriptor.tool_pack,
            policy=self._tool_policies.get(name, descriptor.policy),
        )

    @tool(
        name="get_remote_agent_cards",
        description=("获取可远程调用的Agent卡片信息, 包含Agent id、名称、描述、技能、请求端点等。"),
        parameters={},
        required=[],
    )
    async def get_remote_agent_cards(self) -> ToolResult:
        if not self.manager:
            return ToolResult(success=False, message="A2A工具未初始化")
        agent_cards = [
            {"id": card_id, **agent_card}
            for card_id, agent_card in self.manager.agent_cards.items()
            if agent_card.get("enabled", True)
        ]
        return ToolResult(
            success=True,
            message="获取Agent卡片信息列表成功",
            data=agent_cards,
        )

    @tool(
        name="call_remote_agent",
        description=("根据传递的id+query(分配给远程Agent完成的任务query)调用远程Agent完成对应需求"),
        parameters={
            "id": {
                "type": "string",
                "description": (
                    "需要调用远程agent的id, 格式参考get_remote_agent_cards()返回的数据结构"
                ),
            },
            "query": {
                "type": "string",
                "description": "需要分配给该远程Agent实现的任务/需求query",
            },
        },
        required=["id", "query"],
    )
    async def call_remote_agent(self, id: str, query: str) -> ToolResult:
        if not self.manager:
            return ToolResult(success=False, message="A2A工具未初始化")
        return await self.manager.invoke(agent_id=id, query=query)

    async def cleanup(self) -> None:
        if not self._uses_pool and self.manager is not None:
            await self.manager.cleanup()
        self.manager = None
        self._initialized = False
        self._uses_pool = False
        self._tool_policies = {}
        self._tools_cache = None


__all__ = ["A2ATool"]
