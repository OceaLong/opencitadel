"""Filters for immutable MCP/A2A runtime contracts."""

from app.domain.models.integration_runtime import A2ARuntime, MCPRuntime


def filter_enabled_mcp_runtime(runtime: MCPRuntime) -> MCPRuntime:
    return MCPRuntime(
        servers={
            server_id: server for server_id, server in runtime.servers.items() if server.enabled
        }
    )


def filter_enabled_a2a_runtime(runtime: A2ARuntime) -> A2ARuntime:
    return A2ARuntime(servers=tuple(server for server in runtime.servers if server.enabled))


def filter_mcp_runtime_by_refs(
    runtime: MCPRuntime,
    server_refs: list[str] | tuple[str, ...] | None = None,
) -> MCPRuntime:
    enabled = filter_enabled_mcp_runtime(runtime)
    if not server_refs:
        return enabled
    refs = frozenset(server_refs)
    return MCPRuntime(
        servers={
            server_id: server for server_id, server in enabled.servers.items() if server_id in refs
        }
    )


def filter_a2a_runtime_by_refs(
    runtime: A2ARuntime,
    server_refs: list[str] | tuple[str, ...] | None = None,
) -> A2ARuntime:
    enabled = filter_enabled_a2a_runtime(runtime)
    if not server_refs:
        return enabled
    refs = frozenset(server_refs)
    return A2ARuntime(servers=tuple(server for server in enabled.servers if server.id in refs))


__all__ = [
    "filter_a2a_runtime_by_refs",
    "filter_enabled_a2a_runtime",
    "filter_enabled_mcp_runtime",
    "filter_mcp_runtime_by_refs",
]
