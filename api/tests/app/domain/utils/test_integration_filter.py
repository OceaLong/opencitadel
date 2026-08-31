from app.domain.models.integration_runtime import (
    A2ARuntime,
    A2AServerRuntime,
    MCPRuntime,
    MCPServerRuntime,
)
from app.domain.utils.integration_filter import (
    filter_a2a_runtime_by_refs,
    filter_enabled_a2a_runtime,
    filter_enabled_mcp_runtime,
    filter_mcp_runtime_by_refs,
)


def _mcp(server_id: str, name: str, *, enabled: bool = True) -> MCPServerRuntime:
    return MCPServerRuntime(
        id=server_id,
        name=name,
        url=f"http://{name}",
        enabled=enabled,
    )


def test_filter_enabled_mcp_runtime() -> None:
    runtime = MCPRuntime(
        servers={
            "id-a": _mcp("id-a", "a"),
            "id-b": _mcp("id-b", "b", enabled=False),
        }
    )

    assert list(filter_enabled_mcp_runtime(runtime).servers) == ["id-a"]


def test_filter_enabled_a2a_runtime() -> None:
    runtime = A2ARuntime(
        servers=(
            A2AServerRuntime(id="1", base_url="http://a", enabled=True),
            A2AServerRuntime(id="2", base_url="http://b", enabled=False),
        )
    )

    assert [item.id for item in filter_enabled_a2a_runtime(runtime).servers] == ["1"]


def test_filter_mcp_runtime_by_stable_id_refs() -> None:
    # Regression: MCP refs are stable ids (matching the UI writes and the runtime
    # resolver record.id filter), not display names. Filtering by display name
    # silently matched zero servers, so skill-bound MCP tools vanished.
    runtime = MCPRuntime(
        servers={
            "id-a": _mcp("id-a", "a"),
            "id-b": _mcp("id-b", "b"),
        }
    )

    assert list(filter_mcp_runtime_by_refs(runtime, ["id-a"]).servers) == ["id-a"]
    assert list(filter_mcp_runtime_by_refs(runtime, ["a"]).servers) == []


def test_filter_a2a_runtime_by_stable_id_refs() -> None:
    runtime = A2ARuntime(
        servers=(
            A2AServerRuntime(id="1", base_url="http://a"),
            A2AServerRuntime(id="2", base_url="http://b"),
        )
    )

    assert [item.id for item in filter_a2a_runtime_by_refs(runtime, ["2"]).servers] == ["2"]
