from app.application.ports.crypto import OutboundNetworkPolicy
from app.domain.external.connection_pool import (
    A2AConnectionPoolPort,
    MCPConnectionPoolPort,
)
from app.domain.models.integration_runtime import A2ARuntime, MCPRuntime
from app.domain.runtime_policy import ActivityExecutionPolicy
from app.infrastructure.external.tools.a2a_client import A2AClientManager
from app.infrastructure.external.tools.connection_pool import (
    A2AConnectionPool,
    MCPConnectionPool,
)
from app.infrastructure.external.tools.mcp_client import MCPClientManager


class InfrastructureMCPConnectionPoolAdapter(MCPConnectionPoolPort):
    def __init__(self, *, outbound_policy: OutboundNetworkPolicy) -> None:
        self._pool = MCPConnectionPool(outbound_policy=outbound_policy)

    def try_get_cached(
        self,
        runtime: MCPRuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> MCPClientManager | None:
        return self._pool.try_get_cached(runtime, policy=policy)

    async def refresh_in_background(
        self,
        runtime: MCPRuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> None:
        await self._pool.refresh_in_background(runtime, policy=policy)

    async def acquire(
        self,
        runtime: MCPRuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> MCPClientManager:
        return await self._pool.acquire(runtime, policy=policy)

    async def release_stale(self) -> None:
        await self._pool.release_stale()


class InfrastructureA2AConnectionPoolAdapter(A2AConnectionPoolPort):
    def __init__(self, *, outbound_policy: OutboundNetworkPolicy) -> None:
        self._pool = A2AConnectionPool(outbound_policy=outbound_policy)

    def try_get_cached(
        self,
        runtime: A2ARuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> A2AClientManager | None:
        return self._pool.try_get_cached(runtime, policy=policy)

    async def refresh_in_background(
        self,
        runtime: A2ARuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> None:
        await self._pool.refresh_in_background(runtime, policy=policy)

    async def acquire(
        self,
        runtime: A2ARuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> A2AClientManager:
        return await self._pool.acquire(runtime, policy=policy)

    async def release_stale(self) -> None:
        await self._pool.release_stale()
