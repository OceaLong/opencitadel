#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.models.app_config import MCPConfig, MCPServerConfig, MCPTransport
from app.domain.services.tools.mcp import MCPClientManager, MCPTool


@pytest.mark.asyncio
async def test_connect_mcp_server_safely_records_connection_errors():
    manager = MCPClientManager(
        mcp_config=MCPConfig(
            mcpServers={
                "bad-server": MCPServerConfig(
                    transport=MCPTransport.STREAMABLE_HTTP,
                    url="https://example.invalid/mcp",
                    enabled=True,
                ),
            },
        ),
    )

    with patch.object(
        manager,
        "_connect_mcp_server",
        new=AsyncMock(side_effect=RuntimeError("connection refused")),
    ):
        await manager._connect_mcp_server_safely("bad-server", manager._mcp_config.mcpServers["bad-server"])

    assert manager.connection_errors == {"bad-server": "connection refused"}


@pytest.mark.asyncio
async def test_cache_mcp_server_tools_records_list_tools_errors():
    manager = MCPClientManager(mcp_config=MCPConfig())
    session = AsyncMock()
    session.list_tools = AsyncMock(side_effect=TimeoutError("timed out"))

    await manager._cache_mcp_server_tools("slow-server", session)

    assert manager.connection_errors == {"slow-server": "timed out"}
    assert manager.tools["slow-server"] == []


def test_mcp_tool_connection_errors_empty_without_manager():
    tool = MCPTool(connection_pool=AsyncMock())
    assert tool.connection_errors == {}
