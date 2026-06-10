#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Pre-warmed sandbox container pool and activity tracking for idle cleanup."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Optional

from app.infrastructure.external.sandbox.docker_sandbox import get_sandbox_runtime_settings

if TYPE_CHECKING:
    from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox

logger = logging.getLogger(__name__)

_SANDBOX_ACTIVITY_PREFIX = "sandbox:last_active:"
_SANDBOX_ACTIVITY_TTL_SECONDS = 86400


class SandboxPool:
    """Maintains a queue of warmed sandbox containers ready for assignment."""

    _instance: Optional["SandboxPool"] = None

    def __init__(self) -> None:
        sandbox = get_sandbox_runtime_settings()
        self._enabled = bool(
            not sandbox.address
            and sandbox.pool_enabled
            and sandbox.pool_size > 0
        )
        self._pool_size = max(0, sandbox.pool_size)
        self._queue: asyncio.Queue[DockerSandbox] = asyncio.Queue(maxsize=self._pool_size)
        self._warmup_task: Optional[asyncio.Task] = None
        self._started = False

    @classmethod
    def get_instance(cls) -> "SandboxPool":
        if cls._instance is None:
            cls._instance = SandboxPool()
        return cls._instance

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def start(self) -> None:
        if os.environ.get("MANUS_PROCESS_ROLE", "api") != "worker":
            return
        if not self._enabled or self._started:
            return
        self._started = True
        self._warmup_task = asyncio.create_task(self._warmup_loop())
        logger.info("Sandbox pool started (target_size=%s)", self._pool_size)

    async def shutdown(self) -> None:
        if self._warmup_task:
            self._warmup_task.cancel()
            try:
                await self._warmup_task
            except asyncio.CancelledError:
                pass
            self._warmup_task = None
        while not self._queue.empty():
            sandbox = self._queue.get_nowait()
            await sandbox.destroy()
        self._started = False

    async def acquire(self) -> "DockerSandbox":
        from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox

        if not self._enabled:
            return await DockerSandbox._create_and_warm()

        try:
            sandbox = self._queue.get_nowait()
            await self.touch_activity(sandbox.id)
            return sandbox
        except asyncio.QueueEmpty:
            self.refill_background()
            return await DockerSandbox._create_and_fast_warm()

    def refill_background(self) -> None:
        if self._started and (self._warmup_task is None or self._warmup_task.done()):
            self._warmup_task = asyncio.create_task(self._warmup_loop())

    async def _warmup_loop(self) -> None:
        from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox

        while True:
            try:
                if self._queue.qsize() < self._pool_size:
                    sandbox = await DockerSandbox._create_and_warm()
                    await self._queue.put(sandbox)
                    logger.debug("Sandbox pool warmed container: %s", sandbox.id)
                else:
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Sandbox pool warmup failed: %s", exc)
                await asyncio.sleep(10)

    @staticmethod
    async def touch_activity(container_name: str) -> None:
        if not container_name or container_name == "my-manus-sandbox":
            return
        try:
            from app.infrastructure.storage.redis import get_redis

            redis = get_redis().client
            key = f"{_SANDBOX_ACTIVITY_PREFIX}{container_name}"
            await redis.set(key, str(int(time.time())), ex=_SANDBOX_ACTIVITY_TTL_SECONDS)
        except Exception as exc:
            logger.debug("Failed to record sandbox activity for %s: %s", container_name, exc)

    @staticmethod
    async def get_last_active(container_name: str) -> Optional[int]:
        try:
            from app.infrastructure.storage.redis import get_redis

            redis = get_redis().client
            value = await redis.get(f"{_SANDBOX_ACTIVITY_PREFIX}{container_name}")
            return int(value) if value else None
        except Exception:
            return None


def get_sandbox_pool() -> SandboxPool:
    return SandboxPool.get_instance()
