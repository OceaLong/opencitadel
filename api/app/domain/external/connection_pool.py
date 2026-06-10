#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.domain.models.app_config import A2AConfig, MCPConfig

if TYPE_CHECKING:
    from app.domain.services.tools.a2a import A2AClientManager
    from app.domain.services.tools.mcp import MCPClientManager


@runtime_checkable
class MCPConnectionPoolPort(Protocol):
    async def acquire(self, mcp_config: MCPConfig) -> "MCPClientManager":
        ...

    async def release_stale(self) -> None:
        ...


@runtime_checkable
class A2AConnectionPoolPort(Protocol):
    async def acquire(self, a2a_config: A2AConfig) -> "A2AClientManager":
        ...

    async def release_stale(self) -> None:
        ...
