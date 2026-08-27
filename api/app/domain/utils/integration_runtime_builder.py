"""Build immutable runtime Integration contracts from persisted records."""

from app.domain.models.integration_runtime import (
    A2ARuntime,
    A2AServerRuntime,
    MCPRuntime,
    MCPServerRuntime,
)
from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord


def mcp_records_to_runtime(records: list[MCPServerRecord]) -> MCPRuntime:
    return MCPRuntime(
        servers={
            record.id: MCPServerRuntime(
                id=record.id,
                name=record.name,
                transport=record.transport,
                enabled=record.enabled,
                description=record.description,
                env=record.env,
                command=record.command,
                args=tuple(record.args) if record.args is not None else None,
                url=record.url,
                headers=record.headers,
                transport_options=record.transport_options,
                tool_policies=record.tool_policies,
            )
            for record in records
        }
    )


def a2a_records_to_runtime(records: list[A2AServerRecord]) -> A2ARuntime:
    return A2ARuntime(
        servers=tuple(
            A2AServerRuntime(
                id=record.id,
                base_url=record.base_url,
                enabled=record.enabled,
                tool_policies=record.tool_policies,
            )
            for record in records
        )
    )


__all__ = ["a2a_records_to_runtime", "mcp_records_to_runtime"]
