#!/usr/bin/env python
# -*- coding: utf-8 -*-
import hashlib
import logging
import os
import re
import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Optional, Dict, List, Any, Tuple

from mcp import ClientSession, Tool, StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

from app.application.errors.exceptions import NotFoundError
from app.application.services.config_provider import get_runtime_config
from app.domain.external.connection_pool import MCPConnectionPoolPort
from app.domain.models.app_config import MCPConfig, MCPServerConfig, MCPTransport
from app.domain.models.tool_result import ToolResult
from app.domain.utils.app_config_filter import filter_enabled_mcp_config
from app.domain.utils.mcp_url import validate_mcp_http_url
from .base import BaseTool

"""
MCP客户端管理器的开发思路:
1.在Agent执行的过程中，有可能需要调用多次工具,
  但是因为MCP工具的每次获取都需要调用客户端会话的list_tools()方法,
  非常耗时, 所以需要我们缓存工具的参数信息, 只有在初始化的时候才调用一次,
  并且在销毁MCP客户端管理器的时候一并清除;
2.在前端UI交互中, 无论MCP服务是否启动, 都会显示工具列表信息,
  但是在Agent执行的过程中, 我们只会传递已启动的MCP服务,
  所以对于MCP客户端管理器来说, 可以根据接收的MCP配置的差异加载不同的服务器,
  而不是仅从配置文件中读取数据;
3.MCP客户端管理器会同时管理多个MCP服务, 有可能有stdio、sse、streamable_http等传输协议.
  需要根据传输协议的不同来创建客户端会话(ClientSession), 同时缓存会话;
4.另外有可能有一些环境变量是存储在我们整个系统中的, 在初始化MCP服务的时候，需要将传递进来的
  环境变量与系统的环境变量进行合并后传递给MCP服务;
5.使用AsyncExitStack异步上下文管理器来管理上下文，避免使用with多层嵌套;
6.MCPClientManager的初始化非常耗时, 所以需要有机制可以判断避免重复初始化;
7.由于config.yaml是直接暴露在项目中的, 所以在使用config.yaml进行初始化的时候必须二次校验;
8.同时缓存ClientSession+Tool-Schema, 一个是客户端会话, 一个是工具参数声明;
9.MCP客户端管理器在清除/停止使用的时候, 必须关闭异步上下文管理器、清除资源(ClientSession、Tool-Schema)、
  初始化标识等, 从而避免资源泄露;
"""

logger = logging.getLogger(__name__)

_MAX_TOOL_NAME_LEN = 64
_INVALID_TOOL_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def _sanitize_segment(segment: str) -> str:
    return _INVALID_TOOL_NAME_CHARS.sub("_", segment)


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
    truncated_tool = sanitized_tool[:max(1, max_tool_len)]
    return f"{compact_prefix}_{truncated_tool}"


class MCPClientManager:
    """MCP客户端管理器"""

    def __init__(self, mcp_config: Optional[MCPConfig] = None) -> None:
        """构造函数，完成MCP客户端管理器的初步初始化"""
        self._mcp_config: MCPConfig = mcp_config or MCPConfig()
        self._exit_stack: Optional[AsyncExitStack] = None
        self._clients: Dict[str, ClientSession] = {}
        self._tools: Dict[str, List[Tool]] = {}
        self._canonical_to_source: Dict[str, Tuple[str, str]] = {}
        self._connection_errors: Dict[str, str] = {}
        self._initialized: bool = False
        self._owner_task: Optional[asyncio.Task] = None
        self._ready_event: Optional[asyncio.Event] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._closed_event: Optional[asyncio.Event] = None

    @property
    def tools(self) -> Dict[str, List[Tool]]:
        """只读属性，返回缓存的MCP工具参数声明，键就是服务名字，值就是服务对应的工具声明"""
        return self._tools

    @property
    def connection_errors(self) -> Dict[str, str]:
        """只读属性，返回连接失败的 MCP 服务及错误信息"""
        return dict(self._connection_errors)

    @staticmethod
    def _connect_read_timeout() -> timedelta:
        return timedelta(seconds=get_runtime_config().worker.mcp_connect_timeout_seconds)

    @staticmethod
    def _tool_call_read_timeout() -> timedelta:
        return timedelta(seconds=get_runtime_config().worker.tool_timeout_seconds)

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
            enabled_count = len([
                name for name, cfg in self._mcp_config.mcpServers.items() if cfg.enabled
            ])
            logger.info("从运行时配置加载了 %s 个 MCP 服务器", enabled_count)
            await self._connect_mcp_servers()
            self._initialized = True
            logger.info("MCP客户端管理器加载成功")
        except Exception as exc:
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
        except Exception as exc:
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
        enabled_servers = [
            (server_name, server_config)
            for server_name, server_config in self._mcp_config.mcpServers.items()
            if server_config.enabled
        ]
        await asyncio.gather(*[
            self._connect_mcp_server_safely(server_name, server_config)
            for server_name, server_config in enabled_servers
        ])

    async def _connect_mcp_server_safely(self, server_name: str, server_config: MCPServerConfig) -> None:
        try:
            await self._connect_mcp_server(server_name, server_config)
        except Exception as e:
            error_msg = str(e)
            logger.error(f"连接MCP服务器[{server_name}]出错: {error_msg}")
            self._connection_errors[server_name] = error_msg
            return

    async def _connect_mcp_server(self, server_name: str, server_config: MCPServerConfig) -> None:
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
        except Exception as e:
            # 3.记录日志并抛出异常
            logger.error(f"连接MCP服务器[{server_name}]出错: {str(e)}")
            raise

    async def _connect_stdio_server(self, server_name: str, server_config: MCPServerConfig) -> None:
        """根据服务名字+配置连接stdio服务"""
        # 1.从配置中提取相关命令信息
        command = server_config.command
        args = server_config.args
        env = server_config.env or {}

        # 2.检查command是否存在
        if not command:
            raise ValueError("连接stdio-mcp服务器需要配置command命令")

        # 3.构建stdio连接参数
        server_parameters = StdioServerParameters(
            command=command,
            args=args,
            env={**os.environ, **env},
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
            logger.info(f"连接stdio-mcp服务器成功: {server_name}")
        except Exception as e:
            # 记录错误日志并直接抛出异常
            logger.error(f"连接stdio-mcp服务器失败: {str(e)}")
            raise

    async def _connect_sse_server(self, server_name: str, server_config: MCPServerConfig) -> None:
        """根据服务名字+配置连接sse服务"""
        url = server_config.url
        if not url:
            raise ValueError("连接sse-mcp服务器需要配置url")
        validate_mcp_http_url(url, context=f"MCP 服务[{server_name}] URL")

        try:
            sse_transport = await self._exit_stack.enter_async_context(
                sse_client(url=url, headers=server_config.headers),
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
            logger.info(f"连接sse-mcp服务器成功: {server_name}")
        except Exception as e:
            # 7.记录错误日志并直接抛出异常
            logger.error(f"连接sse-mcp服务器失败: {str(e)}")
            raise

    async def _connect_streamable_http_server(self, server_name: str, server_config: MCPServerConfig) -> None:
        """根据服务名字+配置连接streamable-http服务"""
        url = server_config.url
        if not url:
            raise ValueError("连接streamable-http-mcp服务器需要配置url")
        validate_mcp_http_url(url, context=f"MCP 服务[{server_name}] URL")

        try:
            streamable_http_transport = await self._exit_stack.enter_async_context(
                streamablehttp_client(url=url, headers=server_config.headers),
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
            logger.info(f"连接streamable-http-mcp服务器成功: {server_name}")
        except Exception as e:
            # 7.记录错误日志并直接抛出异常
            logger.error(f"连接streamable-http-mcp服务器失败: {str(e)}")
            raise

    async def _cache_mcp_server_tools(self, server_name: str, session: ClientSession) -> None:
        """根据传递的服务名字+会话缓存mcp服务工具列表"""
        try:
            tools_response = await session.list_tools()
            tools = tools_response.tools if tools_response else []
            self._tools[server_name] = tools
            logger.info(f"MCP服务器[{server_name}]提供了{len(tools)}个工具")
        except Exception as e:
            # 记录日志并将缓存设置为空
            error_msg = str(e)
            logger.error(f"获取MCP服务器[{server_name}]工具列表失败: {error_msg}")
            self._connection_errors[server_name] = error_msg
            self._tools[server_name] = []

    async def get_all_tools(self) -> List[Dict[str, Any]]:
        """获取所有MCP工具列表，返回LLM可以使用的工具参数声明列表并处理MCP的名字"""
        all_tools: List[Dict[str, Any]] = []
        self._canonical_to_source.clear()

        for server_name, tools in self._tools.items():
            for tool in tools:
                tool_name = build_mcp_tool_name(server_name, tool.name)
                if tool_name in self._canonical_to_source:
                    suffix = hashlib.sha256(
                        f"{server_name}:{tool.name}".encode("utf-8")
                    ).hexdigest()[:6]
                    base = tool_name[: max(1, _MAX_TOOL_NAME_LEN - len(suffix) - 1)]
                    tool_name = f"{base}_{suffix}"
                self._canonical_to_source[tool_name] = (server_name, tool.name)

                tool_schema = {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": f"[{server_name}] {tool.description or tool.name}",
                        "parameters": tool.inputSchema,
                    }
                }
                all_tools.append(tool_schema)

        return all_tools

    def _resolve_tool_source(self, tool_name: str) -> Tuple[Optional[str], Optional[str]]:
        mapped = self._canonical_to_source.get(tool_name)
        if mapped:
            return mapped

        for server_name in self._mcp_config.mcpServers.keys():
            expected_prefix = server_name if server_name.startswith("mcp_") else f"mcp_{server_name}"
            if tool_name.startswith(f"{expected_prefix}_"):
                return server_name, tool_name[len(expected_prefix) + 1:]
        return None, None

    async def invoke(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
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

            # 9.判断结果是否存在执行不同的操作
            if result:
                # 10.处理MCP工具生成的content
                content = []
                if hasattr(result, "content") and result.content:
                    for item in result.content:
                        if hasattr(item, "text"):
                            content.append(item.text)
                        else:
                            content.append(str(item))

                # 11.返回工具结果
                return ToolResult(
                    success=True,
                    data="\n".join(content) if content else "工具执行成功"
                )
            else:
                return ToolResult(success=True, data="工具执行成功")
        except Exception as e:
            # 记录错误日志并返回失败的工具结果
            logger.error(f"调用MCP工具[{tool_name}]失败: {str(e)}")
            return ToolResult(
                success=False,
                message=f"调用MCP工具[{tool_name}]失败: {str(e)}",
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
            except Exception as exc:
                logger.warning("等待 MCP owner task 结束失败: %s", exc)
        self._owner_task = None
        self._ready_event = None
        self._shutdown_event = None
        self._closed_event = None


class MCPTool(BaseTool):
    """MCP工具包，包含所有已配置+已启动的MCP工具"""
    name: str = "mcp"

    def __init__(self, connection_pool: MCPConnectionPoolPort) -> None:
        """构造函数，完成MCP工具包的初始化"""
        super().__init__()
        self._connection_pool = connection_pool
        self._initialized: bool = False
        self._tools: List[Dict[str, Any]] = []
        self._manager: Optional[MCPClientManager] = None
        self._uses_pool: bool = False
        self._init_errors: Dict[str, str] = {}

    async def initialize(self, mcp_config: Optional[MCPConfig] = None) -> None:
        """初始化MCP工具包（软失败，不向外抛异常）"""
        if self._initialized:
            return
        filtered = filter_enabled_mcp_config(mcp_config) if mcp_config else MCPConfig()
        try:
            self._manager = await self._connection_pool.acquire(filtered)
            self._uses_pool = True
            self._tools = await self._manager.get_all_tools()
        except Exception as exc:
            logger.warning("MCP 工具包初始化失败: %s", exc)
            self._init_errors["__init__"] = str(exc)
            self._manager = None
            self._tools = []
            self._uses_pool = False
        self._initialized = True

    def get_tools(self) -> List[Dict[str, Any]]:
        """同步获取工具包下的所有工具列表"""
        return self._tools

    @property
    def connection_errors(self) -> Dict[str, str]:
        """返回连接失败的 MCP 服务及错误信息"""
        errors = dict(self._init_errors)
        if self._manager is not None:
            errors.update(self._manager.connection_errors)
        return errors

    def has_tool(self, tool_name: str) -> bool:
        """传递工具名字判断工具是否存在"""
        # 1.循环遍历所有的工具
        for tool in self._tools:
            # 2.判断工具的名字是否存在，如果是则返回True，否则返回False
            if tool["function"]["name"] == tool_name:
                return True

        return False

    async def invoke(self, tool_name: str, **kwargs) -> ToolResult:
        """传递工具名字+参数调用MCP工具并获取结果"""
        if self._manager is None:
            return ToolResult(success=False, message="MCP工具未初始化")
        return await self._manager.invoke(tool_name, kwargs)

    async def cleanup(self) -> None:
        """清除MCP工具资源（连接池模式下仅重置本地状态）"""
        if self._uses_pool:
            self._tools = []
            self._manager = None
            self._initialized = False
            self._uses_pool = False
            return
        if self._manager:
            await self._manager.cleanup()
        self._tools = []
        self._manager = None
        self._initialized = False
