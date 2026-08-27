from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.models.integration_runtime import MCPRuntime, MCPServerRuntime, MCPTransport
from app.domain.services.tools.mcp import MCPTool
from app.infrastructure.external.tools.mcp_client import MCPClientManager


@pytest.mark.asyncio
async def test_connect_mcp_server_safely_records_connection_errors():
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
        await manager._connect_mcp_server_safely(
            "bad-server", manager._runtime.servers["server-id"]
        )

    assert manager.connection_errors == {"bad-server": "connection refused"}


@pytest.mark.asyncio
async def test_cache_mcp_server_tools_records_list_tools_errors():
    manager = MCPClientManager(
        runtime=MCPRuntime(),
        connect_timeout=timedelta(seconds=7),
        tool_timeout=timedelta(seconds=11),
    )
    session = AsyncMock()
    session.list_tools = AsyncMock(side_effect=TimeoutError("timed out"))

    await manager._cache_mcp_server_tools("slow-server", session)

    assert manager.connection_errors == {"slow-server": "timed out"}
    assert manager.tools["slow-server"] == []


def test_mcp_tool_connection_errors_empty_without_manager():
    tool = MCPTool(connection_pool=AsyncMock())
    assert tool.connection_errors == {}
