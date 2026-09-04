"""A2A transport and Agent Card lifecycle implementation."""

import asyncio
import json
import logging
import uuid
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

import httpx

from app.application.ports.crypto import OutboundNetworkPolicy
from app.domain.errors import ServerRequestsError
from app.domain.models.integration_runtime import A2ARuntime
from app.domain.models.tool_result import ToolResult
from app.domain.utils.outbound_url import resolve_outbound_url
from app.infrastructure.security.outbound_http import (
    DEFAULT_OUTBOUND_NETWORK_POLICY,
    create_ssrf_safe_async_client,
)

logger = logging.getLogger(__name__)

_MAX_AGENT_CARD_BYTES = 1024 * 1024
_MAX_A2A_RESPONSE_BYTES = 5 * 1024 * 1024

"""
A2A客户端管理器的开发思路:
1.在Agent执行过程中, 有可能需要多次调用Remote-Agent，
  但是a2a中的agent-card.json请求是网络io, 相对耗时，
  所以需要缓存agent-card的相关信息, 只有在初始化A2A客户端的时候才初始化一次,
  更新a2a服务器的时候更新, 清除a2a客户端管理器时删除;
2.在前端UI交互中, 无论A2A服务器是否启动, 都会展示Card信息,
  但是呢, 在执行/规划Agent中, 我们只传递启用的A2A服务, 所以A2A客户端管理器必须动态接受配置;
3.一个A2A客户端会同时管理多个Agent, 但是不同的A2A服务有可能他们的name是一样的，
  需要考虑传递给Agent信息时的唯一性, 会配置多一个唯一的id;
4.由于使用httpx客户端, 这个客户端需要创建上下文/释放资源, 所以可以使用AsyncExitStack来管理
  异步上下文, 避免大量使用with..as的嵌套组合;
5.A2AClientManager的初始化非常耗时, 一次请求中只初始化一次;
6.客户端只接受已校验、不可变的 Integration 运行时快照;
7.A2A客户端管理器只实现两个方法, 一个是get_remote_agent_cards、call_remote_agent;
8.A2A客户端管理器停止时必须清除对应资源, 涵盖了缓存, 异步上下文管理器避免资源泄露;
"""


class A2AClientManager:
    """A2A客户端管理器"""

    def __init__(
        self,
        runtime: A2ARuntime | None = None,
        *,
        connect_timeout: timedelta,
        tool_timeout: timedelta,
        outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
    ) -> None:
        """构造函数，完成A2A客户端的初始化"""
        self._runtime = runtime or A2ARuntime()
        if connect_timeout.total_seconds() <= 0 or tool_timeout.total_seconds() <= 0:
            raise ValueError("A2A timeouts must be positive")
        self._connect_timeout = connect_timeout
        self._tool_timeout = tool_timeout
        self._outbound_policy = outbound_policy
        self._exit_stack: AsyncExitStack = AsyncExitStack()  # 上下文管理器
        self._httpx_client: httpx.AsyncClient | None = None  # httpx客户端
        self._agent_cards: dict[str, Any] = {}  # agent卡片
        self._initialized: bool = False  # 是否初始化

    @property
    def agent_cards(self) -> dict[str, Any]:
        """只读属性，返回agent卡片信息"""
        return self._agent_cards

    async def initialize(self) -> None:
        """异步初始化函数，用于初始化所有已配置的a2a服务"""
        # 1.检测是否已经初始化
        if self._initialized:
            return

        try:
            # 3.初始化httpx客户端
            self._httpx_client = await self._exit_stack.enter_async_context(
                create_ssrf_safe_async_client(
                    timeout=httpx.Timeout(
                        self._tool_timeout.total_seconds(),
                        connect=self._connect_timeout.total_seconds(),
                    ),
                    follow_redirects=False,
                    outbound_policy=self._outbound_policy,
                ),
            )

            # 4.记录日志并连接所有配置的a2a服务获取卡片信息
            logger.info("加载%s个A2A服务", len(self._runtime.servers))
            await self._get_a2a_agent_cards()
            self._initialized = True
            logger.info("A2A客户端加载成功")
        except (OSError, RuntimeError, ValueError) as exc:
            logger.error("A2A客户端管理器加载失败")
            raise ServerRequestsError("A2A客户端管理器加载失败") from exc

    async def _get_a2a_agent_cards(self) -> None:
        """根据配置连接所有已启用的 a2a 服务器获取 AgentCard 信息"""
        enabled_servers = [
            server_config for server_config in self._runtime.servers if server_config.enabled
        ]
        await asyncio.gather(
            *[self._load_a2a_agent_card(server_config) for server_config in enabled_servers]
        )

    async def _load_a2a_agent_card(self, a2a_server_config) -> None:
        try:
            base_url = resolve_outbound_url(
                a2a_server_config.base_url,
                allowed_ports=self._outbound_policy.allowed_ports,
                allow_private_hosts=self._outbound_policy.allow_private_hosts,
            ).url.rstrip("/")
            # 2.调用httpx客户端发起请求
            agent_card_response = await self._httpx_client.get(
                f"{base_url}/.well-known/agent-card.json"
            )
            agent_card_response.raise_for_status()
            self._ensure_response_size(
                agent_card_response,
                _MAX_AGENT_CARD_BYTES,
            )
            agent_card = agent_card_response.json()
            if agent_card.get("url"):
                resolve_outbound_url(
                    str(agent_card["url"]),
                    allowed_ports=self._outbound_policy.allowed_ports,
                    allow_private_hosts=self._outbound_policy.allow_private_hosts,
                )

            # 3.存储到agent_cards
            agent_card["enabled"] = a2a_server_config.enabled
            self._agent_cards[a2a_server_config.id] = agent_card
        except (OSError, RuntimeError, ValueError) as e:
            logger.warning("加载A2A服务[%s]失败: %s", a2a_server_config.id, e)
            return

    async def invoke(self, agent_id: str, query: str) -> ToolResult:
        """根据传递的智能体id+query调用Remote-Agent"""
        if agent_id not in self._agent_cards:
            return ToolResult(success=False, message="该远程Agent不存在")

        agent_card = self._agent_cards.get(agent_id, {})
        if not agent_card.get("enabled", True):
            return ToolResult(success=False, message="该远程Agent已禁用")
        url = agent_card.get("url", "")

        # 3.判断端点是否存在
        if url == "":
            return ToolResult(success=False, message="该远程Agent调用端点不存在")
        try:
            url = resolve_outbound_url(
                str(url),
                allowed_ports=self._outbound_policy.allowed_ports,
                allow_private_hosts=self._outbound_policy.allow_private_hosts,
            ).url
        except ValueError as exc:
            return ToolResult(
                success=False,
                message=f"远程Agent端点未通过出站安全策略: {exc}",
            )

        payload = self._build_message_payload(query)
        try:
            # 4.根据 AgentCard 能力选择流式或非流式调用
            if agent_card.get("capabilities", {}).get("streaming", False):
                result = await self._invoke_stream(url, payload)
            else:
                result = await self._invoke_send(url, payload)
            text = self._extract_text(result)
            return ToolResult(
                success=True,
                message="调用远程Agent成功",
                data={"text": text, "raw": result} if text else result,
            )
        except (OSError, RuntimeError, ValueError) as e:
            # failure_kind=transport 供连接池统计连续失败并强制重建（P2-9）。
            logger.error("调用远程Agent[%s:%s]出错: %s", agent_id, url, e)
            return ToolResult(
                success=False,
                message=f"调用远程Agent[{agent_id}:{url}]出错: {e!s}",
                failure_kind="transport",
            )

    @staticmethod
    def _build_message_payload(query: str) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "role": "user",
                    "parts": [
                        {"kind": "text", "text": query},
                    ],
                },
            },
        }

    async def _invoke_send(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        agent_response = await self._httpx_client.post(url, json=payload)
        agent_response.raise_for_status()
        self._ensure_response_size(agent_response, _MAX_A2A_RESPONSE_BYTES)
        return agent_response.json()

    async def _invoke_stream(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        stream_payload = {**payload, "method": "message/stream"}
        events = []
        total_bytes = 0
        async with self._httpx_client.stream("POST", url, json=stream_payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                total_bytes += len(line.encode("utf-8"))
                if total_bytes > _MAX_A2A_RESPONSE_BYTES:
                    raise ValueError("A2A 流式响应超过允许大小")
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if not line or line == "[DONE]":
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    events.append({"text": line})
        return {"events": events}

    @staticmethod
    def _ensure_response_size(response: httpx.Response, limit: int) -> None:
        declared = response.headers.get("content-length")
        if declared:
            try:
                if int(declared) > limit:
                    raise ValueError("A2A 响应超过允许大小")
            except ValueError as exc:
                if "超过允许大小" in str(exc):
                    raise
        if len(response.content) > limit:
            raise ValueError("A2A 响应超过允许大小")

    @classmethod
    def _extract_text(cls, payload: Any) -> str:
        texts = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                if isinstance(value.get("text"), str):
                    texts.append(value["text"])
                for key in ("result", "message", "artifact", "parts", "events"):
                    if key in value:
                        visit(value[key])
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(payload)
        return "\n".join(text for text in texts if text).strip()

    async def cleanup(self) -> None:
        """当退出A2A客户端管理器时，清除对应资源"""
        try:
            await self._exit_stack.aclose()
            self._agent_cards.clear()
            self._initialized = False
            logger.info("清除A2A客户端管理器成功")
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("清理A2A客户端管理器失败: %s", e)


__all__ = ["A2AClientManager"]
