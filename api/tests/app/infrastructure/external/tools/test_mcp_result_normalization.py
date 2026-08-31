from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.integration_runtime import MCPRuntime
from app.infrastructure.external.tools.mcp_client import MCPClientManager


def _manager_with_session(result) -> MCPClientManager:
    manager = MCPClientManager(
        runtime=MCPRuntime(),
        connect_timeout=timedelta(seconds=7),
        tool_timeout=timedelta(seconds=11),
    )
    manager._canonical_to_source["mcp_collector_probe"] = ("collector", "probe")
    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=result)
    manager._clients["collector"] = session
    return manager


@pytest.mark.asyncio
async def test_invoke_preserves_structured_mcp_tool_result() -> None:
    envelope = {
        "status": "ok",
        "data": {"healthy": True},
        "request_id": "request-1",
    }
    manager = _manager_with_session(
        SimpleNamespace(structuredContent=envelope, content=[], isError=False)
    )

    result = await manager.invoke("mcp_collector_probe", {"probe_id": "api"})

    assert result.success is True
    assert result.data == envelope


@pytest.mark.asyncio
async def test_invoke_maps_mcp_error_result_to_failed_tool_result() -> None:
    manager = _manager_with_session(
        SimpleNamespace(
            structuredContent=None,
            content=[SimpleNamespace(text="probe denied")],
            isError=True,
        )
    )

    result = await manager.invoke("mcp_collector_probe", {"probe_id": "forbidden"})

    assert result.success is False
    assert result.message == "probe denied"
    assert result.data is None
