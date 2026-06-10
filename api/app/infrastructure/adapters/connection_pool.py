#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.external.connection_pool import A2AConnectionPoolPort, MCPConnectionPoolPort
from app.domain.models.app_config import A2AConfig, MCPConfig
from app.domain.services.tools.a2a import A2AClientManager
from app.domain.services.tools.mcp import MCPClientManager
from app.infrastructure.external.tools.connection_pool import A2AConnectionPool, MCPConnectionPool


class InfrastructureMCPConnectionPoolAdapter(MCPConnectionPoolPort):
    async def acquire(self, mcp_config: MCPConfig) -> MCPClientManager:
        return await MCPConnectionPool.acquire(mcp_config)

    async def release_stale(self) -> None:
        await MCPConnectionPool.release_stale()


class InfrastructureA2AConnectionPoolAdapter(A2AConnectionPoolPort):
    async def acquire(self, a2a_config: A2AConfig) -> A2AClientManager:
        return await A2AConnectionPool.acquire(a2a_config)

    async def release_stale(self) -> None:
        await A2AConnectionPool.release_stale()
