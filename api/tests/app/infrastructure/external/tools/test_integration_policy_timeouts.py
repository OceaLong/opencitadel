from datetime import timedelta

from app.domain.models.integration_runtime import A2ARuntime, MCPRuntime
from app.domain.runtime_policy import ActivityExecutionPolicy
from app.infrastructure.external.tools.a2a_client import A2AClientManager
from app.infrastructure.external.tools.connection_pool import _config_fingerprint
from app.infrastructure.external.tools.mcp_client import MCPClientManager


def test_integration_clients_require_exact_policy_derived_timeouts() -> None:
    connect_timeout = timedelta(seconds=13)
    tool_timeout = timedelta(seconds=47)

    mcp = MCPClientManager(
        MCPRuntime(),
        connect_timeout=connect_timeout,
        tool_timeout=tool_timeout,
    )
    a2a = A2AClientManager(
        A2ARuntime(),
        connect_timeout=connect_timeout,
        tool_timeout=tool_timeout,
    )

    assert mcp._connect_read_timeout() == connect_timeout
    assert mcp._tool_call_read_timeout() == tool_timeout
    assert a2a._connect_timeout == connect_timeout
    assert a2a._tool_timeout == tool_timeout


def test_connection_pool_identity_includes_frozen_activity_policy() -> None:
    config = MCPRuntime()

    assert _config_fingerprint(
        config,
        ActivityExecutionPolicy(tool_timeout_seconds=47),
    ) != _config_fingerprint(
        config,
        ActivityExecutionPolicy(tool_timeout_seconds=48),
    )
