from __future__ import annotations

from typing import Any, Protocol

from app.domain.models.integration_server import MCPServerRecord
from app.domain.runtime_policy import ActivityExecutionPolicy

ACTUATOR_MCP_SERVER_NAME = "ops-actuator"


class PatrolActuatorPort(Protocol):
    async def get_capabilities(
        self,
        server: MCPServerRecord,
        *,
        policy: ActivityExecutionPolicy,
        timeout_seconds: int = 15,
    ) -> dict[str, Any]: ...

    async def execute_action(
        self,
        server: MCPServerRecord,
        tool: str,
        arguments: dict[str, Any],
        *,
        policy: ActivityExecutionPolicy,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]: ...
