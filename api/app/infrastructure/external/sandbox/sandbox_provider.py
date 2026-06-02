#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Sandbox provider abstraction for Docker/K8s orchestration."""
from abc import ABC, abstractmethod
from typing import Optional, Type

from app.domain.external.sandbox import Sandbox


class SandboxProvider(ABC):
    @abstractmethod
    async def create(self) -> Sandbox:
        raise NotImplementedError

    @abstractmethod
    async def get(self, sandbox_id: str) -> Optional[Sandbox]:
        raise NotImplementedError

    @abstractmethod
    async def warmup(self, count: int = 1) -> None:
        """Pre-create sandbox instances to reduce cold start latency."""
        raise NotImplementedError


class DockerSandboxProvider(SandboxProvider):
    def __init__(self, sandbox_cls: Type[Sandbox]) -> None:
        self._sandbox_cls = sandbox_cls
        self._warm_pool: list[Sandbox] = []

    async def create(self) -> Sandbox:
        if self._warm_pool:
            return self._warm_pool.pop()
        return await self._sandbox_cls.create()

    async def get(self, sandbox_id: str) -> Optional[Sandbox]:
        return await self._sandbox_cls.get(sandbox_id)

    async def warmup(self, count: int = 1) -> None:
        for _ in range(count):
            sandbox = await self._sandbox_cls.create()
            self._warm_pool.append(sandbox)


class K8sSandboxProvider(SandboxProvider):
    """K8s sandbox provider stub — delegates to Docker provider until cluster integration."""

    def __init__(self, fallback: SandboxProvider) -> None:
        self._fallback = fallback

    async def create(self) -> Sandbox:
        return await self._fallback.create()

    async def get(self, sandbox_id: str) -> Optional[Sandbox]:
        return await self._fallback.get(sandbox_id)

    async def warmup(self, count: int = 1) -> None:
        await self._fallback.warmup(count)
