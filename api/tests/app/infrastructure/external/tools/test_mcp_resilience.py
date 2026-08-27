import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.models.integration_runtime import MCPRuntime, MCPServerRuntime, MCPTransport
from app.domain.runtime_policy import ActivityExecutionPolicy
from app.domain.services.tools.mcp import MCPTool
from app.infrastructure.external.tools.mcp_client import MCPClientManager


@pytest.mark.asyncio
async def test_mcp_client_manager_initialize_soft_fail_on_connect_error():
    manager = MCPClientManager(
        runtime=MCPRuntime(
            servers={
                "server-id": MCPServerRuntime(
                    id="server-id",
                    name="bad-server",
                    transport=MCPTransport.STREAMABLE_HTTP,
                    url="https://example.invalid/mcp",
                    enabled=True,
                ),
            },
        ),
        connect_timeout=timedelta(seconds=7),
        tool_timeout=timedelta(seconds=11),
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

    await tool.initialize(MCPRuntime(), policy=ActivityExecutionPolicy())

    assert tool.connection_errors["__init__"] == "pool unavailable"
    assert tool.get_tools() == []


@pytest.mark.asyncio
async def test_mcp_client_manager_cleanup_from_different_task():
    manager = MCPClientManager(
        runtime=MCPRuntime(),
        connect_timeout=timedelta(seconds=7),
        tool_timeout=timedelta(seconds=11),
    )
    connect_mock = AsyncMock()

    with patch.object(manager, "_connect_mcp_servers", new=connect_mock):
        await manager.initialize()

    assert connect_mock.await_count == 1

    async def cleanup_from_other_task():
        await manager.cleanup()

    await asyncio.create_task(cleanup_from_other_task())
    assert manager._owner_task is None or manager._owner_task.done()
