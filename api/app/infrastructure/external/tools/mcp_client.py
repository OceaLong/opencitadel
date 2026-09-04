# MCP SDK is loaded lazily because it is execution-kernel-only.
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from contextlib import AsyncExitStack
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from app.application.ports.crypto import OutboundNetworkPolicy
from app.domain.errors import NotFoundError
from app.domain.models.integration_runtime import (
    MCPRuntime,
    MCPServerRuntime,
    MCPTransport,
)
from app.domain.models.tool_policy import (
    CONSERVATIVE_TOOL_POLICY,
    ToolExecutionPolicy,
)
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.untrusted import json_depth, truncate_and_wrap
from app.domain.utils.mcp_url import validate_mcp_http_url
from app.infrastructure.security.outbound_http import (
    DEFAULT_OUTBOUND_NETWORK_POLICY,
    create_ssrf_safe_mcp_client,
)

if TYPE_CHECKING:
    from mcp import ClientSession, Tool

"""Transport lifecycle for an already-validated MCP Integration runtime.

The manager owns sessions and tool-schema caches for one immutable snapshot.
Stable IDs select resources while display names remain the agent tool namespace.
"""

logger = logging.getLogger(__name__)

_MAX_TOOL_NAME_LEN = 64
_INVALID_TOOL_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
# 信任边界（D11）：远端工具 description 截断上限与 inputSchema 结构上限。
_MAX_TOOL_DESCRIPTION_CHARS = 512
_MAX_INPUT_SCHEMA_DEPTH = 8
_MAX_INPUT_SCHEMA_BYTES = 16 * 1024


def _sanitize_segment(segment: str) -> str:
    return _INVALID_TOOL_NAME_CHARS.sub("_", segment)


def _input_schema_within_limits(input_schema: Any) -> bool:
    try:
        serialized = json.dumps(input_schema, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return False
    if len(serialized.encode("utf-8")) > _MAX_INPUT_SCHEMA_BYTES:
        return False
    return json_depth(input_schema) <= _MAX_INPUT_SCHEMA_DEPTH


def build_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """生成符合 OpenAI 函数名约束的 MCP 工具名，并保证唯一性。"""
    if server_name.startswith("mcp_"):
        prefix = _sanitize_segment(server_name)
    else:
        prefix = f"mcp_{_sanitize_segment(server_name)}"
    sanitized_tool = _sanitize_segment(tool_name)
    candidate = f"{prefix}_{sanitized_tool}"
    if len(candidate) <= _MAX_TOOL_NAME_LEN:
        return candidate

    server_hash = hashlib.sha256(server_name.encode("utf-8")).hexdigest()[:8]
    compact_prefix = f"mcp_{server_hash}"
    max_tool_len = _MAX_TOOL_NAME_LEN - len(compact_prefix) - 1
    truncated_tool = sanitized_tool[: max(1, max_tool_len)]
    return f"{compact_prefix}_{truncated_tool}"


class MCPClientManager:
    """MCP客户端管理器"""

    def __init__(
        self,
        runtime: MCPRuntime | None = None,
        *,
        connect_timeout: timedelta,
        tool_timeout: timedelta,
        outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
    ) -> None:
        """构造函数，完成MCP客户端管理器的初步初始化"""
        self._runtime = runtime or MCPRuntime()
        if connect_timeout.total_seconds() <= 0 or tool_timeout.total_seconds() <= 0:
            raise ValueError("MCP timeouts must be positive")
        self._connect_timeout = connect_timeout
        self._tool_timeout = tool_timeout
        self._outbound_policy = outbound_policy
        self._exit_stack: AsyncExitStack | None = None
        self._clients: dict[str, ClientSession] = {}
        self._tools: dict[str, list[Tool]] = {}
        self._canonical_to_source: dict[str, tuple[str, str]] = {}
        self._connection_errors: dict[str, str] = {}
        self._initialized: bool = False
        self._owner_task: asyncio.Task | None = None
        self._ready_event: asyncio.Event | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._closed_event: asyncio.Event | None = None

    @property
    def tools(self) -> dict[str, list[Tool]]:
        """只读属性，返回缓存的MCP工具参数声明，键就是服务名字，值就是服务对应的工具声明"""
        return self._tools

    @property
    def connection_errors(self) -> dict[str, str]:
        """只读属性，返回连接失败的 MCP 服务及错误信息"""
        return dict(self._connection_errors)

    def _connect_read_timeout(self) -> timedelta:
        return self._connect_timeout

    def _tool_call_read_timeout(self) -> timedelta:
        return self._tool_timeout

    def _validate_http_target(self, url: str, *, context: str) -> None:
        validate_mcp_http_url(
            url,
            context=context,
            allowed_ports=self._outbound_policy.allowed_ports,
            allow_private_hosts=self._outbound_policy.allow_private_hosts,
        )

    async def initialize(self) -> None:
        """初始化函数，用于连接所有配置的MCP服务器（软失败，不向外抛异常）"""
        if self._initialized and self._owner_task and not self._owner_task.done():
            return
        if self._owner_task and not self._owner_task.done():
            await self._ready_event.wait()
            return

        self._ready_event = asyncio.Event()
        self._shutdown_event = asyncio.Event()
        self._closed_event = asyncio.Event()
        self._owner_task = asyncio.create_task(self._owner_lifecycle())
        await self._ready_event.wait()

    async def _owner_lifecycle(self) -> None:
        """在专用 owner task 中进入/退出 MCP 客户端上下文，避免跨任务 cancel scope 错误。"""
        self._exit_stack = AsyncExitStack()
        try:
            enabled_count = len(
                [server for server in self._runtime.servers.values() if server.enabled]
            )
            logger.info("从运行时配置加载了 %s 个 MCP 服务器", enabled_count)
            await self._connect_mcp_servers()
            self._initialized = True
            logger.info("MCP客户端管理器加载成功")
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.error("MCP客户端管理器加载失败: %s", exc)
            self._connection_errors["__init__"] = str(exc)
            self._initialized = True
        finally:
            self._ready_event.set()

        await self._shutdown_event.wait()

        try:
            if self._exit_stack is not None:
                await self._exit_stack.aclose()
            logger.info("清除MCP客户端管理器成功")
        except (OSError, RuntimeError, ValueError) as exc:
            logger.error("清理MCP客户端管理器失败: %s", exc)
        finally:
            self._clients.clear()
            self._tools.clear()
            self._canonical_to_source.clear()
            self._connection_errors.clear()
            self._exit_stack = None
            self._initialized = False
            self._closed_event.set()

    async def _connect_mcp_servers(self) -> None:
        """根据配置连接所有已启用的 MCP 服务"""
        enabled_servers = [server for server in self._runtime.servers.values() if server.enabled]
        await asyncio.gather(
            *[self._connect_mcp_server_safely(server.name, server) for server in enabled_servers]
        )

    async def _connect_mcp_server_safely(
        self, server_name: str, server_config: MCPServerRuntime
    ) -> None:
        try:
            await self._connect_mcp_server(server_name, server_config)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as e:
            error_msg = str(e)
            logger.error("连接MCP服务器[%s]出错: %s", server_name, error_msg)
            self._connection_errors[server_name] = error_msg
            return

    async def _connect_mcp_server(
        self,
        server_name: str,
        server_config: MCPServerRuntime,
    ) -> None:
        """根据传递的服务名字+服务配置连接到单个MCP服务"""
        try:
            # 1.获取mcp服务的传输协议
            transport = server_config.transport

            # 2.根据不同的传输协议调用不同的方法连接MCP服务器
            if transport == MCPTransport.STDIO:
                await self._connect_stdio_server(server_name, server_config)
            elif transport == MCPTransport.SSE:
                await self._connect_sse_server(server_name, server_config)
            elif transport == MCPTransport.STREAMABLE_HTTP:
                await self._connect_streamable_http_server(server_name, server_config)
            else:
                raise ValueError(f"MCP服务[{server_name}]使用了不支持的传输协议: {transport}")
        except (OSError, RuntimeError, ValueError) as e:
            # 3.记录日志并抛出异常
            logger.error("连接MCP服务器[%s]出错: %s", server_name, e)
            raise

    async def _connect_stdio_server(
        self,
        server_name: str,
        server_config: MCPServerRuntime,
    ) -> None:
        """根据服务名字+配置连接stdio服务"""
        # 延迟导入:mcp 是 worker 专用重库,api 进程不装
        from mcp import ClientSession, StdioServerParameters, stdio_client

        # 1.从配置中提取相关命令信息
        command = server_config.command
        args = server_config.args
        env = server_config.env or {}

        # 2.检查command是否存在
        if not command:
            raise ValueError("连接stdio-mcp服务器需要配置command命令")

        # 3.构建stdio连接参数
        # Only forward a minimal, non-secret base environment plus the
        # operator-configured env. Never leak the kernel's full os.environ
        # (DB passwords, API_KEY_SECRET, signing keys, storage credentials) to a
        # stdio MCP subprocess.
        safe_env = {
            key: os.environ[key]
            for key in (
                "PATH",
                "HOME",
                "LANG",
                "LC_ALL",
                "LC_CTYPE",
                "TZ",
                "TMPDIR",
                "TEMP",
                "TMP",
            )
            if key in os.environ
        }
        safe_env.update(env)
        server_parameters = StdioServerParameters(
            command=command,
            args=args,
            env=safe_env,
        )

        try:
            # 4.使用异步上下文管理器创建传输协议
            stdio_transport = await self._exit_stack.enter_async_context(
                stdio_client(server_parameters),
            )
            read_stream, write_stream = stdio_transport

            # 5.根据读取与写入流构建会话
            session: ClientSession = await self._exit_stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=self._connect_read_timeout(),
                ),
            )

            # 6.初始化MCP服务会话
            await session.initialize()

            # 7.缓存对应的mcp连接客户端
            self._clients[server_name] = session

            # 8.缓存对应mcp服务的工具列表
            await self._cache_mcp_server_tools(server_name, session)
            logger.info("连接stdio-mcp服务器成功: %s", server_name)
        except (OSError, RuntimeError, ValueError) as e:
            # 记录错误日志并直接抛出异常
            logger.error("连接stdio-mcp服务器失败: %s", e)
            raise

    async def _connect_sse_server(
        self,
        server_name: str,
        server_config: MCPServerRuntime,
    ) -> None:
        """根据服务名字+配置连接sse服务"""
        # 延迟导入:mcp 是 worker 专用重库,api 进程不装
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        url = server_config.url
        if not url:
            raise ValueError("连接sse-mcp服务器需要配置url")
        self._validate_http_target(url, context=f"MCP 服务[{server_name}] URL")

        try:
            sse_transport = await self._exit_stack.enter_async_context(
                sse_client(
                    url=url,
                    headers=server_config.headers,
                    httpx_client_factory=partial(
                        create_ssrf_safe_mcp_client,
                        outbound_policy=self._outbound_policy,
                    ),
                ),
            )
            read_stream, write_stream = sse_transport

            # 3.创建客户端会话
            session: ClientSession = await self._exit_stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=self._connect_read_timeout(),
                ),
            )

            # 4.初始化MCP服务会话
            await session.initialize()

            # 5.缓存对应的mcp连接客户端
            self._clients[server_name] = session

            # 6.缓存对应mcp服务的工具列表
            await self._cache_mcp_server_tools(server_name, session)
            logger.info("连接sse-mcp服务器成功: %s", server_name)
        except (OSError, RuntimeError, ValueError) as e:
            # 7.记录错误日志并直接抛出异常
            logger.error("连接sse-mcp服务器失败: %s", e)
            raise

    async def _connect_streamable_http_server(
        self,
        server_name: str,
        server_config: MCPServerRuntime,
    ) -> None:
        """根据服务名字+配置连接streamable-http服务"""
        # 延迟导入:mcp 是 worker 专用重库,api 进程不装
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        url = server_config.url
        if not url:
            raise ValueError("连接streamable-http-mcp服务器需要配置url")
        self._validate_http_target(url, context=f"MCP 服务[{server_name}] URL")

        try:
            streamable_http_transport = await self._exit_stack.enter_async_context(
                streamablehttp_client(
                    url=url,
                    headers=server_config.headers,
                    httpx_client_factory=partial(
                        create_ssrf_safe_mcp_client,
                        outbound_policy=self._outbound_policy,
                    ),
                ),
            )

            # 3.streamable-http模型需要解包获取输入与输出流
            if len(streamable_http_transport) == 3:
                read_stream, write_stream, _ = streamable_http_transport
            else:
                read_stream, write_stream = streamable_http_transport

            # 4.创建客户端会话
            session: ClientSession = await self._exit_stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=self._connect_read_timeout(),
                ),
            )

            # 5.初始化MCP服务会话
            await session.initialize()

            # 6.缓存对应的mcp连接客户端
            self._clients[server_name] = session

            # 7.缓存对应mcp服务的工具列表
            await self._cache_mcp_server_tools(server_name, session)
            logger.info("连接streamable-http-mcp服务器成功: %s", server_name)
        except (OSError, RuntimeError, ValueError) as e:
            # 7.记录错误日志并直接抛出异常
            logger.error("连接streamable-http-mcp服务器失败: %s", e)
            raise

    async def _cache_mcp_server_tools(self, server_name: str, session: ClientSession) -> None:
        """根据传递的服务名字+会话缓存mcp服务工具列表"""
        try:
            tools_response = await session.list_tools()
            tools = tools_response.tools if tools_response else []
            self._tools[server_name] = tools
            logger.info("MCP服务器[%s]提供了%s个工具", server_name, len(tools))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
            # 记录日志并将缓存设置为空
            error_msg = str(e)
            logger.error("获取MCP服务器[%s]工具列表失败: %s", server_name, error_msg)
            self._connection_errors[server_name] = error_msg
            self._tools[server_name] = []

    async def get_all_tools(self) -> list[dict[str, Any]]:
        """获取所有MCP工具列表，返回LLM可以使用的工具参数声明列表并处理MCP的名字"""
        all_tools: list[dict[str, Any]] = []
        self._canonical_to_source.clear()

        for server_name, tools in self._tools.items():
            for tool in tools:
                # 信任边界（D11）：inputSchema 超深/超大的远端工具直接拒绝，
                # 避免把不可信的巨型 schema 注入模型上下文。
                if not _input_schema_within_limits(tool.inputSchema):
                    logger.warning(
                        "MCP服务器[%s]的工具[%s] inputSchema 超出限制（深度≤%s、序列化≤%s字节），已拒绝",
                        server_name,
                        tool.name,
                        _MAX_INPUT_SCHEMA_DEPTH,
                        _MAX_INPUT_SCHEMA_BYTES,
                    )
                    continue
                tool_name = build_mcp_tool_name(server_name, tool.name)
                if tool_name in self._canonical_to_source:
                    suffix = hashlib.sha256(f"{server_name}:{tool.name}".encode()).hexdigest()[:6]
                    base = tool_name[: max(1, _MAX_TOOL_NAME_LEN - len(suffix) - 1)]
                    tool_name = f"{base}_{suffix}"
                self._canonical_to_source[tool_name] = (server_name, tool.name)

                description = truncate_and_wrap(
                    str(tool.description or tool.name),
                    max_chars=_MAX_TOOL_DESCRIPTION_CHARS,
                )
                tool_schema = {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": f"[{server_name}] {description}",
                        "parameters": tool.inputSchema,
                    },
                }
                all_tools.append(tool_schema)

        return all_tools

    def _resolve_tool_source(self, tool_name: str) -> tuple[str | None, str | None]:
        mapped = self._canonical_to_source.get(tool_name)
        if mapped:
            return mapped

        for server in self._runtime.servers.values():
            server_name = server.name
            expected_prefix = (
                server_name if server_name.startswith("mcp_") else f"mcp_{server_name}"
            )
            if tool_name.startswith(f"{expected_prefix}_"):
                return server_name, tool_name[len(expected_prefix) + 1 :]
        return None, None

    def get_tool_policy(self, tool_name: str) -> ToolExecutionPolicy:
        """Resolve administrator-owned metadata for one canonical MCP function."""
        server_name, source_name = self._resolve_tool_source(tool_name)
        if not server_name or not source_name:
            return CONSERVATIVE_TOOL_POLICY
        server = next(
            (item for item in self._runtime.servers.values() if item.name == server_name),
            None,
        )
        if server is None:
            return CONSERVATIVE_TOOL_POLICY
        return server.tool_policies.get(source_name, CONSERVATIVE_TOOL_POLICY)

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """根据传递的工具名字+参数调用MCP工具"""
        try:
            original_server_name, original_tool_name = self._resolve_tool_source(tool_name)

            if not original_server_name or not original_tool_name:
                raise NotFoundError(f"服务器解析MCP工具不存在: {tool_name}")

            # 7.获取该工具所属的会话
            session = self._clients.get(original_server_name)
            if not session:
                return ToolResult(success=False, message=f"MCP服务器[{original_server_name}]未连接")

            # 8.使用会话调用工具
            result = await session.call_tool(
                original_tool_name,
                arguments,
                read_timeout_seconds=self._tool_call_read_timeout(),
            )

            # 9. Preserve the MCP result contract: structuredContent is the
            # canonical machine-readable channel and content is display text.
            if result:
                content: list[str] = []
                for item in result.content or []:
                    text = getattr(item, "text", None)
                    content.append(text if isinstance(text, str) else str(item))
                rendered = "\n".join(content)

                if result.isError:
                    return ToolResult(
                        success=False,
                        message=rendered or "MCP工具执行失败",
                    )
                if result.structuredContent is not None:
                    return ToolResult(success=True, data=result.structuredContent)
                return ToolResult(success=True, data=rendered or "工具执行成功")
            return ToolResult(success=True, data="工具执行成功")
        except (OSError, RuntimeError, ValueError) as e:
            # 记录错误日志并返回失败的工具结果；failure_kind=transport 供连接池
            # 统计连续失败并在超阈值时强制重建条目（P2-9）。
            logger.error("调用MCP工具[%s]失败: %s", tool_name, e)
            return ToolResult(
                success=False,
                message=f"调用MCP工具[{tool_name}]失败: {e!s}",
                failure_kind="transport",
            )

    async def cleanup(self) -> None:
        """关闭 MCP 连接；在 owner task 内完成 AsyncExitStack 退出。"""
        if self._owner_task is None or self._closed_event is None:
            return
        if self._closed_event.is_set():
            return

        self._shutdown_event.set()
        await self._closed_event.wait()
        if not self._owner_task.done():
            try:
                await self._owner_task
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("等待 MCP owner task 结束失败: %s", exc)
        self._owner_task = None
        self._ready_event = None
        self._shutdown_event = None
        self._closed_event = None


__all__ = ["MCPClientManager", "build_mcp_tool_name"]
