#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Redis per-node sandbox quota admission policy."""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

from app.infrastructure.external.runtime_settings import AdmissionRuntimeSettings
from app.infrastructure.external.sandbox.memory_probe import (
    get_host_available_mb,
    memory_meets_threshold,
)
from app.infrastructure.external.sandbox.node_id import resolve_node_id

logger = logging.getLogger(__name__)

_HOLDER_TTL_SECONDS = 300
_HOLDER_PREFIX = "quota:sandbox:holder:"
_NODE_INUSE_PREFIX = "quota:sandbox:node:"
_GLOBAL_INUSE_KEY = "quota:sandbox:global:inuse"
_GLOBAL_CAPACITY_KEY = "quota:sandbox:global:capacity"

_admission_settings = AdmissionRuntimeSettings()
_acquire_lock = asyncio.Lock()


def configure_admission_runtime(settings: AdmissionRuntimeSettings) -> None:
    global _admission_settings
    _admission_settings = settings


def get_admission_runtime_settings() -> AdmissionRuntimeSettings:
    return _admission_settings


class AdmissionPolicy(ABC):
    @abstractmethod
    async def can_admit(self) -> bool:
        ...

    @abstractmethod
    async def acquire(self, holder_id: str) -> bool:
        ...

    @abstractmethod
    async def release(self, holder_id: str) -> None:
        ...

    @abstractmethod
    async def heartbeat(self, holder_id: str) -> None:
        ...

    @abstractmethod
    async def reconcile(self, live_holder_ids: set[str]) -> None:
        ...


class SandboxQuota(AdmissionPolicy):
    """Redis-backed per-node sandbox quota with optional global cap."""

    def __init__(self) -> None:
        self._node_id = resolve_node_id()

    @property
    def node_id(self) -> str:
        return self._node_id

    def _settings(self) -> AdmissionRuntimeSettings:
        return get_admission_runtime_settings()

    def _node_inuse_key(self) -> str:
        return f"{_NODE_INUSE_PREFIX}{self._node_id}:inuse"

    def _node_capacity_key(self) -> str:
        return f"{_NODE_INUSE_PREFIX}{self._node_id}:capacity"

    def _holder_key(self, holder_id: str) -> str:
        return f"{_HOLDER_PREFIX}{self._node_id}:{holder_id}"

    async def _publish_quota_metrics(self) -> None:
        try:
            redis = await self._redis()
            inuse = int(await redis.get(self._node_inuse_key()) or 0)
            from app.infrastructure.observability.admission_metrics import set_quota_inuse

            set_quota_inuse(self._node_id, inuse)
        except Exception:
            pass

    async def _redis(self):
        from app.infrastructure.storage.redis import get_redis

        return get_redis().client

    async def _redis_available(self) -> bool:
        try:
            redis = await self._redis()
            await redis.ping()
            return True
        except Exception:
            return False

    def _should_check_memory(self) -> bool:
        settings = self._settings()
        if settings.sandbox_driver == "kubernetes":
            return False
        return True

    async def can_admit(self) -> bool:
        if not await self._redis_available():
            from app.infrastructure.observability.admission_metrics import record_admission_rejected

            record_admission_rejected("redis_unavailable")
            return False
        settings = self._settings()
        if self._should_check_memory():
            if not memory_meets_threshold(settings.admission_min_host_available_mb):
                from app.infrastructure.observability.admission_metrics import record_admission_rejected

                record_admission_rejected("memory_low")
                return False
        try:
            redis = await self._redis()
            await redis.set(self._node_capacity_key(), settings.max_sandboxes_per_node)
            if settings.max_dynamic_sandboxes_global > 0:
                await redis.set(_GLOBAL_CAPACITY_KEY, settings.max_dynamic_sandboxes_global)
            inuse = int(await redis.get(self._node_inuse_key()) or 0)
            if inuse >= settings.max_sandboxes_per_node:
                return False
            if settings.max_dynamic_sandboxes_global > 0:
                global_inuse = int(await redis.get(_GLOBAL_INUSE_KEY) or 0)
                if global_inuse >= settings.max_dynamic_sandboxes_global:
                    return False
            return True
        except Exception as exc:
            logger.warning("can_admit check failed: %s", exc)
            return False

    async def acquire(self, holder_id: str) -> bool:
        if not holder_id:
            return False
        async with _acquire_lock:
            if not await self.can_admit():
                return False
            settings = self._settings()
            try:
                redis = await self._redis()
                holder_key = self._holder_key(holder_id)
                if await redis.exists(holder_key):
                    await redis.expire(holder_key, _HOLDER_TTL_SECONDS)
                    return True
                inuse = int(await redis.get(self._node_inuse_key()) or 0)
                if inuse >= settings.max_sandboxes_per_node:
                    return False
                if settings.max_dynamic_sandboxes_global > 0:
                    global_inuse = int(await redis.get(_GLOBAL_INUSE_KEY) or 0)
                    if global_inuse >= settings.max_dynamic_sandboxes_global:
                        return False
                pipe = redis.pipeline()
                pipe.incr(self._node_inuse_key())
                if settings.max_dynamic_sandboxes_global > 0:
                    pipe.incr(_GLOBAL_INUSE_KEY)
                pipe.set(holder_key, str(int(time.time())), ex=_HOLDER_TTL_SECONDS)
                await pipe.execute()
                if settings.admission_settle_seconds > 0:
                    await asyncio.sleep(settings.admission_settle_seconds)
                await self._publish_quota_metrics()
                return True
            except Exception as exc:
                logger.warning("quota acquire failed for %s: %s", holder_id, exc)
                return False

    async def release(self, holder_id: str) -> None:
        if not holder_id:
            return
        try:
            redis = await self._redis()
            holder_key = self._holder_key(holder_id)
            if not await redis.exists(holder_key):
                return
            settings = self._settings()
            pipe = redis.pipeline()
            pipe.delete(holder_key)
            pipe.decr(self._node_inuse_key())
            if settings.max_dynamic_sandboxes_global > 0:
                pipe.decr(_GLOBAL_INUSE_KEY)
            await pipe.execute()
            inuse = int(await redis.get(self._node_inuse_key()) or 0)
            if inuse < 0:
                await redis.set(self._node_inuse_key(), 0)
            if settings.max_dynamic_sandboxes_global > 0:
                global_inuse = int(await redis.get(_GLOBAL_INUSE_KEY) or 0)
                if global_inuse < 0:
                    await redis.set(_GLOBAL_INUSE_KEY, 0)
            await self._publish_quota_metrics()
        except Exception as exc:
            logger.warning("quota release failed for %s: %s", holder_id, exc)

    async def heartbeat(self, holder_id: str) -> None:
        if not holder_id:
            return
        try:
            redis = await self._redis()
            holder_key = self._holder_key(holder_id)
            if await redis.exists(holder_key):
                await redis.expire(holder_key, _HOLDER_TTL_SECONDS)
        except Exception as exc:
            logger.debug("quota heartbeat failed for %s: %s", holder_id, exc)

    async def reconcile(self, live_holder_ids: set[str]) -> None:
        """Align Redis holders with live sandbox containers/pods."""
        if not await self._redis_available():
            return
        settings = self._settings()
        try:
            redis = await self._redis()
            await redis.set(self._node_capacity_key(), settings.max_sandboxes_per_node)
            pattern = f"{_HOLDER_PREFIX}{self._node_id}:*"
            known: set[str] = set()
            async for key in redis.scan_iter(match=pattern, count=100):
                suffix = key.split(f"{self._node_id}:", 1)[-1]
                known.add(suffix)
                if suffix not in live_holder_ids:
                    await self.release(suffix)
            for holder_id in live_holder_ids:
                if holder_id not in known:
                    holder_key = self._holder_key(holder_id)
                    if not await redis.exists(holder_key):
                        pipe = redis.pipeline()
                        pipe.incr(self._node_inuse_key())
                        if settings.max_dynamic_sandboxes_global > 0:
                            pipe.incr(_GLOBAL_INUSE_KEY)
                        pipe.set(holder_key, str(int(time.time())), ex=_HOLDER_TTL_SECONDS)
                        await pipe.execute()
            await redis.set(self._node_inuse_key(), len(live_holder_ids))
            if settings.max_dynamic_sandboxes_global > 0:
                total = 0
                async for key in redis.scan_iter(match=f"{_HOLDER_PREFIX}*", count=200):
                    total += 1
                await redis.set(_GLOBAL_INUSE_KEY, total)
            await self._publish_quota_metrics()
        except Exception as exc:
            logger.warning("quota reconcile failed: %s", exc)

    async def list_holders(self) -> set[str]:
        try:
            redis = await self._redis()
            pattern = f"{_HOLDER_PREFIX}{self._node_id}:*"
            holders: set[str] = set()
            async for key in redis.scan_iter(match=pattern, count=100):
                holders.add(key.split(f"{self._node_id}:", 1)[-1])
            return holders
        except Exception:
            return set()


_quota_instance: Optional[SandboxQuota] = None


def get_sandbox_quota() -> SandboxQuota:
    global _quota_instance
    if _quota_instance is None:
        _quota_instance = SandboxQuota()
    return _quota_instance
