"""Shared MCP/A2A connection pools with health checks."""

import asyncio
import hashlib
import json
import logging
import time
from datetime import timedelta

from app.application.ports.crypto import OutboundNetworkPolicy
from app.domain.models.integration_runtime import A2ARuntime, MCPRuntime
from app.domain.runtime_policy import ActivityExecutionPolicy
from app.domain.utils.integration_filter import (
    filter_enabled_a2a_runtime,
    filter_enabled_mcp_runtime,
)
from app.infrastructure.external.tools.a2a_client import A2AClientManager
from app.infrastructure.external.tools.mcp_client import MCPClientManager
from app.infrastructure.security.outbound_http import DEFAULT_OUTBOUND_NETWORK_POLICY

logger = logging.getLogger(__name__)

_POOL_TTL_SECONDS = 300


def _config_fingerprint(config, policy: ActivityExecutionPolicy) -> str:
    payload = {
        "config": config.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class _PoolEntry:
    def __init__(self, manager, fingerprint: str) -> None:
        self.manager = manager
        self.fingerprint = fingerprint
        self.last_used = time.monotonic()
        self.lock = asyncio.Lock()


class MCPConnectionPool:
    """Composition-owned MCP manager pool keyed by enabled-server config hash."""

    def __init__(
        self,
        *,
        outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
    ) -> None:
        self._outbound_policy = outbound_policy
        self._entries: dict[str, _PoolEntry] = {}
        self._lock = asyncio.Lock()

    def try_get_cached(
        self,
        runtime: MCPRuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> MCPClientManager | None:
        """Return a warm pool manager without connecting."""
        filtered = filter_enabled_mcp_runtime(runtime)
        fingerprint = _config_fingerprint(filtered, policy)
        entry = self._entries.get(fingerprint)
        if entry and entry.fingerprint == fingerprint:
            entry.last_used = time.monotonic()
            return entry.manager
        return None

    async def refresh_in_background(
        self,
        runtime: MCPRuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> None:
        """Connect to MCP servers in the background to warm the tool cache."""
        try:
            await self.acquire(runtime, policy=policy)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("Background MCP pool refresh failed: %s", exc)

    async def acquire(
        self,
        runtime: MCPRuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> "MCPClientManager":
        filtered = filter_enabled_mcp_runtime(runtime)
        fingerprint = _config_fingerprint(filtered, policy)
        async with self._lock:
            entry = self._entries.get(fingerprint)
            if entry and entry.fingerprint == fingerprint:
                entry.last_used = time.monotonic()
                return entry.manager

            manager = MCPClientManager(
                runtime=filtered,
                connect_timeout=timedelta(seconds=policy.mcp_connect_timeout_seconds),
                tool_timeout=timedelta(seconds=policy.tool_timeout_seconds),
                outbound_policy=self._outbound_policy,
            )
            await manager.initialize()
            self._entries[fingerprint] = _PoolEntry(manager, fingerprint)
            return manager

    async def invalidate_all(self) -> None:
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            try:
                await entry.manager.cleanup()
            except (OSError, RuntimeError, ValueError) as e:
                logger.warning("MCP pool invalidation failed: %s", e)

    async def release_stale(self, max_idle_seconds: float = _POOL_TTL_SECONDS) -> None:
        now = time.monotonic()
        stale = [
            fp for fp, entry in self._entries.items() if now - entry.last_used > max_idle_seconds
        ]
        for fp in stale:
            entry = self._entries.pop(fp, None)
            if entry:
                try:
                    await entry.manager.cleanup()
                except (OSError, RuntimeError, ValueError) as e:
                    logger.warning("MCP pool cleanup failed: %s", e)


class A2AConnectionPool:
    """Composition-owned A2A manager pool keyed by enabled-server config hash."""

    def __init__(
        self,
        *,
        outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
    ) -> None:
        self._outbound_policy = outbound_policy
        self._entries: dict[str, _PoolEntry] = {}
        self._lock = asyncio.Lock()

    def try_get_cached(
        self,
        runtime: A2ARuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> A2AClientManager | None:
        """Return a warm pool manager without fetching Agent Cards."""
        filtered = filter_enabled_a2a_runtime(runtime)
        fingerprint = _config_fingerprint(filtered, policy)
        entry = self._entries.get(fingerprint)
        if entry and entry.fingerprint == fingerprint:
            entry.last_used = time.monotonic()
            return entry.manager
        return None

    async def refresh_in_background(
        self,
        runtime: A2ARuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> None:
        """Fetch Agent Cards in the background to warm the read projection."""
        try:
            await self.acquire(runtime, policy=policy)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("Background A2A pool refresh failed: %s", exc)

    async def acquire(
        self,
        runtime: A2ARuntime,
        *,
        policy: ActivityExecutionPolicy,
    ) -> "A2AClientManager":
        filtered = filter_enabled_a2a_runtime(runtime)
        fingerprint = _config_fingerprint(filtered, policy)
        async with self._lock:
            entry = self._entries.get(fingerprint)
            if entry and entry.fingerprint == fingerprint:
                entry.last_used = time.monotonic()
                return entry.manager

            manager = A2AClientManager(
                runtime=filtered,
                connect_timeout=timedelta(seconds=policy.mcp_connect_timeout_seconds),
                tool_timeout=timedelta(seconds=policy.tool_timeout_seconds),
                outbound_policy=self._outbound_policy,
            )
            await manager.initialize()
            self._entries[fingerprint] = _PoolEntry(manager, fingerprint)
            return manager

    async def invalidate_all(self) -> None:
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            try:
                await entry.manager.cleanup()
            except (OSError, RuntimeError, ValueError) as e:
                logger.warning("A2A pool invalidation failed: %s", e)

    async def release_stale(self, max_idle_seconds: float = _POOL_TTL_SECONDS) -> None:
        now = time.monotonic()
        stale = [
            fp for fp, entry in self._entries.items() if now - entry.last_used > max_idle_seconds
        ]
        for fp in stale:
            entry = self._entries.pop(fp, None)
            if entry:
                try:
                    await entry.manager.cleanup()
                except (OSError, RuntimeError, ValueError) as e:
                    logger.warning("A2A pool cleanup failed: %s", e)
