#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared MCP/A2A connection pools with health checks."""
import asyncio
import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Dict

from app.domain.models.app_config import A2AConfig, MCPConfig
from app.domain.utils.app_config_filter import filter_enabled_a2a_config, filter_enabled_mcp_config

if TYPE_CHECKING:
    from app.domain.services.tools.a2a import A2AClientManager
    from app.domain.services.tools.mcp import MCPClientManager

logger = logging.getLogger(__name__)

_POOL_TTL_SECONDS = 300


def _config_fingerprint(config) -> str:
    payload = config.model_dump(mode="json")
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class _PoolEntry:
    def __init__(self, manager, fingerprint: str) -> None:
        self.manager = manager
        self.fingerprint = fingerprint
        self.last_used = time.monotonic()
        self.lock = asyncio.Lock()


class MCPConnectionPool:
    """Process-wide MCP manager pool keyed by enabled-server config hash."""

    _entries: Dict[str, _PoolEntry] = {}
    _lock = asyncio.Lock()

    @classmethod
    async def acquire(cls, mcp_config: MCPConfig) -> "MCPClientManager":
        from app.domain.services.tools.mcp import MCPClientManager

        filtered = filter_enabled_mcp_config(mcp_config)
        fingerprint = _config_fingerprint(filtered)
        async with cls._lock:
            entry = cls._entries.get(fingerprint)
            if entry and entry.fingerprint == fingerprint:
                entry.last_used = time.monotonic()
                return entry.manager

            manager = MCPClientManager(mcp_config=filtered)
            await manager.initialize()
            cls._entries[fingerprint] = _PoolEntry(manager, fingerprint)
            return manager

    @classmethod
    async def invalidate_all(cls) -> None:
        async with cls._lock:
            entries = list(cls._entries.values())
            cls._entries.clear()
        for entry in entries:
            try:
                await entry.manager.cleanup()
            except Exception as e:
                logger.warning("MCP pool invalidation failed: %s", e)

    @classmethod
    async def release_stale(cls, max_idle_seconds: float = _POOL_TTL_SECONDS) -> None:
        now = time.monotonic()
        stale = [
            fp for fp, entry in cls._entries.items()
            if now - entry.last_used > max_idle_seconds
        ]
        for fp in stale:
            entry = cls._entries.pop(fp, None)
            if entry:
                try:
                    await entry.manager.cleanup()
                except Exception as e:
                    logger.warning("MCP pool cleanup failed: %s", e)


class A2AConnectionPool:
    """Process-wide A2A manager pool keyed by enabled-server config hash."""

    _entries: Dict[str, _PoolEntry] = {}
    _lock = asyncio.Lock()

    @classmethod
    async def acquire(cls, a2a_config: A2AConfig) -> "A2AClientManager":
        from app.domain.services.tools.a2a import A2AClientManager

        filtered = filter_enabled_a2a_config(a2a_config)
        fingerprint = _config_fingerprint(filtered)
        async with cls._lock:
            entry = cls._entries.get(fingerprint)
            if entry and entry.fingerprint == fingerprint:
                entry.last_used = time.monotonic()
                return entry.manager

            manager = A2AClientManager(a2a_config=filtered)
            await manager.initialize()
            cls._entries[fingerprint] = _PoolEntry(manager, fingerprint)
            return manager

    @classmethod
    async def invalidate_all(cls) -> None:
        async with cls._lock:
            entries = list(cls._entries.values())
            cls._entries.clear()
        for entry in entries:
            try:
                await entry.manager.cleanup()
            except Exception as e:
                logger.warning("A2A pool invalidation failed: %s", e)

    @classmethod
    async def release_stale(cls, max_idle_seconds: float = _POOL_TTL_SECONDS) -> None:
        now = time.monotonic()
        stale = [
            fp for fp, entry in cls._entries.items()
            if now - entry.last_used > max_idle_seconds
        ]
        for fp in stale:
            entry = cls._entries.pop(fp, None)
            if entry:
                try:
                    await entry.manager.cleanup()
                except Exception as e:
                    logger.warning("A2A pool cleanup failed: %s", e)
