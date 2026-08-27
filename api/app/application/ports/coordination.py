"""Application contracts for optional Redis-backed coordination capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RedisConnectivity:
    """One capability observation; PostgreSQL authority is represented elsewhere."""

    available: bool
    error_key: str | None = None


@dataclass(frozen=True)
class RateLimitDecision:
    limited: bool
    connectivity: RedisConnectivity


@runtime_checkable
class RedisConnectivityPort(Protocol):
    async def check(self) -> RedisConnectivity: ...


@runtime_checkable
class LeaseManagerPort(Protocol):
    async def acquire(self, key: str, owner: str, *, ttl_seconds: float) -> bool: ...

    async def renew(self, key: str, owner: str, *, ttl_seconds: float) -> bool: ...

    async def release(self, key: str, owner: str) -> bool: ...


@runtime_checkable
class SandboxQuotaStorePort(Protocol):
    async def available(self) -> RedisConnectivity: ...

    async def can_admit(
        self,
        *,
        node_id: str,
        node_limit: int,
        global_limit: int,
    ) -> bool: ...

    async def acquire(
        self,
        *,
        node_id: str,
        holder_id: str,
        node_limit: int,
        global_limit: int,
        holder_ttl_seconds: int,
    ) -> bool: ...

    async def release(self, *, node_id: str, holder_id: str) -> bool: ...

    async def heartbeat(
        self,
        *,
        node_id: str,
        holder_id: str,
        holder_ttl_seconds: int,
    ) -> bool: ...

    async def reconcile(
        self,
        *,
        node_id: str,
        live_holder_ids: set[str],
        node_limit: int,
        global_limit: int,
        holder_ttl_seconds: int,
    ) -> bool: ...

    async def node_in_use(self, node_id: str) -> int | None: ...


@runtime_checkable
class SandboxActivityStorePort(Protocol):
    async def touch(
        self,
        sandbox_id: str,
        *,
        active_at_epoch: int,
        ttl_seconds: int,
    ) -> bool: ...

    async def last_active(self, sandbox_id: str) -> int | None: ...


@runtime_checkable
class RateLimitStorePort(Protocol):
    async def check_and_record(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision: ...
