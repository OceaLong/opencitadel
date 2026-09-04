from typing import Any, Protocol, runtime_checkable

from app.domain.models.integration_runtime import A2ARuntime, MCPRuntime
from app.domain.models.tool_policy import ToolExecutionPolicy
from app.domain.models.tool_result import ToolResult
from app.domain.runtime_policy import ActivityExecutionPolicy


class MCPClientPort(Protocol):
    @property
    def tools(self) -> dict[str, list[Any]]: ...

    @property
    def connection_errors(self) -> dict[str, str]: ...

    async def get_all_tools(self) -> list[dict[str, Any]]: ...

    def get_tool_policy(self, tool_name: str) -> ToolExecutionPolicy: ...

    async def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult: ...

    async def cleanup(self) -> None: ...


class A2AClientPort(Protocol):
    @property
    def agent_cards(self) -> dict[str, Any]: ...

    async def invoke(self, agent_id: str, query: str) -> ToolResult: ...

    async def cleanup(self) -> None: ...


@runtime_checkable
class MCPConnectionPoolPort(Protocol):
    def try_get_cached(
        self,
        runtime: MCPRuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> MCPClientPort | None: ...

    async def refresh_in_background(
        self,
        runtime: MCPRuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> None: ...

    async def acquire(
        self,
        runtime: MCPRuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> MCPClientPort: ...

    async def report_result(self, manager: MCPClientPort, *, success: bool) -> None: ...

    async def release_stale(self) -> None: ...


@runtime_checkable
class A2AConnectionPoolPort(Protocol):
    def try_get_cached(
        self,
        runtime: A2ARuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> A2AClientPort | None: ...

    async def refresh_in_background(
        self,
        runtime: A2ARuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> None: ...

    async def acquire(
        self,
        runtime: A2ARuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> A2AClientPort: ...

    async def report_result(self, manager: A2AClientPort, *, success: bool) -> None: ...

    async def release_stale(self) -> None: ...
