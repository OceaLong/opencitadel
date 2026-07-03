#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.models.app_config import MCPConfig, MCPServerConfig, MCPTransport
from app.domain.services.tools.mcp import MCPClientManager, MCPTool


@pytest.mark.asyncio
async def test_mcp_client_manager_initialize_soft_fail_on_connect_error():
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
        await manager.initialize()

    assert manager.connection_errors["bad-server"] == "connection refused"
    await manager.cleanup()


@pytest.mark.asyncio
async def test_mcp_tool_initialize_soft_fail_when_pool_acquire_raises():
    pool = AsyncMock()
    pool.acquire = AsyncMock(side_effect=RuntimeError("pool unavailable"))
    tool = MCPTool(connection_pool=pool)

    await tool.initialize(MCPConfig())

    assert tool.connection_errors["__init__"] == "pool unavailable"
    assert tool.get_tools() == []


@pytest.mark.asyncio
async def test_mcp_client_manager_cleanup_from_different_task():
    manager = MCPClientManager(mcp_config=MCPConfig())
    connect_mock = AsyncMock()

    with patch.object(manager, "_connect_mcp_servers", new=connect_mock):
        await manager.initialize()

    assert connect_mock.await_count == 1

    async def cleanup_from_other_task():
        await manager.cleanup()

    await asyncio.create_task(cleanup_from_other_task())
    assert manager._owner_task is None or manager._owner_task.done()
