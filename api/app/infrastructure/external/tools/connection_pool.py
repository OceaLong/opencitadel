"""Shared MCP/A2A connection pools keyed by enabled-server config fingerprint.

Concurrency contract (P2-9): the pool-wide lock only guards the entry map;
each fingerprint owns its own ``_PoolEntry.lock`` so one slow server handshake
never serializes unrelated fingerprints. Entries whose managers keep failing
transport calls (>= ``_MAX_CONSECUTIVE_FAILURES`` consecutive) are invalidated
so the next acquire rebuilds the connection.
"""

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
_MAX_CONSECUTIVE_FAILURES = 3


def _config_fingerprint(config, policy: ActivityExecutionPolicy) -> str:
    payload = {
        "config": config.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class _PoolEntry:
    def __init__(self, fingerprint: str) -> None:
        self.manager = None
        self.fingerprint = fingerprint
        self.last_used = time.monotonic()
        self.failures = 0
        # Per-fingerprint construction lock: connecting one config must not
        # block acquires for other configs.
        self.lock = asyncio.Lock()


class _BaseConnectionPool:
    """Fingerprint-keyed manager pool shared by the MCP and A2A variants."""

    _kind = "integration"

    def __init__(
        self,
        *,
        outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
    ) -> None:
        self._outbound_policy = outbound_policy
        self._entries: dict[str, _PoolEntry] = {}
        self._lock = asyncio.Lock()

    def _filter(self, runtime):
        raise NotImplementedError

    def _create_manager(self, filtered, policy: ActivityExecutionPolicy):
        raise NotImplementedError

    def try_get_cached(self, runtime, *, policy: ActivityExecutionPolicy):
        """Return a warm pool manager without connecting."""
        filtered = self._filter(runtime)
        fingerprint = _config_fingerprint(filtered, policy)
        entry = self._entries.get(fingerprint)
        if entry and entry.manager is not None:
            entry.last_used = time.monotonic()
            return entry.manager
        return None

    async def refresh_in_background(self, runtime, *, policy: ActivityExecutionPolicy) -> None:
        """Connect in the background to warm the pool for this config."""
        try:
            await self.acquire(runtime, policy=policy)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("Background %s pool refresh failed: %s", self._kind, exc)

    async def acquire(self, runtime, *, policy: ActivityExecutionPolicy):
        filtered = self._filter(runtime)
        fingerprint = _config_fingerprint(filtered, policy)
        async with self._lock:
            entry = self._entries.get(fingerprint)
            if entry is None:
                entry = _PoolEntry(fingerprint)
                self._entries[fingerprint] = entry
        async with entry.lock:
            if entry.manager is not None:
                entry.last_used = time.monotonic()
                return entry.manager
            manager = self._create_manager(filtered, policy)
            await manager.initialize()
            entry.manager = manager
            entry.failures = 0
            entry.last_used = time.monotonic()
            return manager

    async def report_result(self, manager, *, success: bool) -> None:
        """Track transport health; invalidate after repeated consecutive failures."""
        async with self._lock:
            entry = next(
                (item for item in self._entries.values() if item.manager is manager),
                None,
            )
            if entry is None:
                return
            if success:
                entry.failures = 0
                return
            entry.failures += 1
            if entry.failures < _MAX_CONSECUTIVE_FAILURES:
                return
            self._entries.pop(entry.fingerprint, None)
        logger.warning(
            "%s pool entry invalidated after %s consecutive transport failures",
            self._kind,
            _MAX_CONSECUTIVE_FAILURES,
        )
        try:
            await manager.cleanup()
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("%s pool invalidation cleanup failed: %s", self._kind, exc)

    async def invalidate_all(self) -> None:
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            if entry.manager is None:
                continue
            try:
                await entry.manager.cleanup()
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("%s pool invalidation failed: %s", self._kind, exc)

    async def release_stale(self, max_idle_seconds: float = _POOL_TTL_SECONDS) -> None:
        now = time.monotonic()
        stale = [
            fp for fp, entry in self._entries.items() if now - entry.last_used > max_idle_seconds
        ]
        for fp in stale:
            entry = self._entries.pop(fp, None)
            if entry and entry.manager is not None:
                try:
                    await entry.manager.cleanup()
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.warning("%s pool cleanup failed: %s", self._kind, exc)


class MCPConnectionPool(_BaseConnectionPool):
    """Composition-owned MCP manager pool keyed by enabled-server config hash."""

    _kind = "MCP"

    def _filter(self, runtime: MCPRuntime) -> MCPRuntime:
        return filter_enabled_mcp_runtime(runtime)

    def _create_manager(
        self,
        filtered: MCPRuntime,
        policy: ActivityExecutionPolicy,
    ) -> MCPClientManager:
        return MCPClientManager(
            runtime=filtered,
            connect_timeout=timedelta(seconds=policy.mcp_connect_timeout_seconds),
            tool_timeout=timedelta(seconds=policy.tool_timeout_seconds),
            outbound_policy=self._outbound_policy,
        )


class A2AConnectionPool(_BaseConnectionPool):
    """Composition-owned A2A manager pool keyed by enabled-server config hash."""

    _kind = "A2A"

    def _filter(self, runtime: A2ARuntime) -> A2ARuntime:
        return filter_enabled_a2a_runtime(runtime)

    def _create_manager(
        self,
        filtered: A2ARuntime,
        policy: ActivityExecutionPolicy,
    ) -> A2AClientManager:
        return A2AClientManager(
            runtime=filtered,
            connect_timeout=timedelta(seconds=policy.mcp_connect_timeout_seconds),
            tool_timeout=timedelta(seconds=policy.tool_timeout_seconds),
            outbound_policy=self._outbound_policy,
        )
