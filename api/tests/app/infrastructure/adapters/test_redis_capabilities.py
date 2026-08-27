from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.application.ports.coordination import (
    LeaseManagerPort,
    RateLimitStorePort,
    SandboxActivityStorePort,
    SandboxQuotaStorePort,
)
from app.application.ports.execution import WakeupMessage
from app.application.ports.streams import WakeupPort
from app.infrastructure.adapters.redis_capabilities import (
    RedisConnectivityProbe,
    RedisLeaseManager,
    RedisSandboxActivityStore,
    RedisSandboxQuotaStore,
    RedisWakeupAdapter,
)


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, tuple[str, int]] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.available = True

    async def ping(self) -> bool:
        if not self.available:
            raise ConnectionError("redis unavailable")
        return True

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
        ex: int | None = None,
    ) -> bool:
        await self.ping()
        if nx and key in self.values:
            return False
        self.values[key] = value
        if px is not None:
            self.expirations[key] = ("px", px)
        if ex is not None:
            self.expirations[key] = ("ex", ex)
        return True

    async def get(self, key: str) -> str | None:
        await self.ping()
        return self.values.get(key)

    async def delete(self, *keys: str) -> int:
        await self.ping()
        removed = 0
        for key in keys:
            removed += int(key in self.values)
            self.values.pop(key, None)
            self.expirations.pop(key, None)
        return removed

    async def eval(self, script: str, key_count: int, *args: Any) -> int:
        await self.ping()
        if "opencitadel:renew-lease" in script:
            assert key_count == 1
            key, owner, ttl_ms = args
            if self.values.get(key) != owner:
                return 0
            self.expirations[key] = ("px", int(ttl_ms))
            return 1
        if "opencitadel:release-lease" in script:
            assert key_count == 1
            key, owner = args
            if self.values.get(key) != owner:
                return 0
            await self.delete(key)
            return 1
        raise AssertionError("unexpected Lua script")

    async def xadd(self, stream: str, values: dict[str, str]) -> str:
        await self.ping()
        entry_id = f"{len(self.streams.get(stream, [])) + 1}-0"
        self.streams.setdefault(stream, []).append((entry_id, values))
        return entry_id

    async def xread(
        self,
        streams: dict[str, str],
        *,
        count: int,
        block: int,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        del block
        await self.ping()
        stream, cursor = next(iter(streams.items()))
        entries = [entry for entry in self.streams.get(stream, []) if entry[0] > cursor]
        return [(stream, entries[:count])] if entries else []

    async def scan_iter(self, *, match: str, count: int) -> AsyncIterator[str]:
        del match, count
        if False:
            yield ""


class FailingRedis(MemoryRedis):
    def __init__(self) -> None:
        super().__init__()
        self.available = False


@pytest.mark.asyncio
async def test_token_owned_lease_cannot_be_renewed_or_released_by_another_owner() -> None:
    redis = MemoryRedis()
    manager = RedisLeaseManager(redis)

    assert isinstance(manager, LeaseManagerPort)
    assert await manager.acquire("lease", "owner-a", ttl_seconds=5)
    assert not await manager.renew("lease", "owner-b", ttl_seconds=5)
    assert not await manager.release("lease", "owner-b")
    assert redis.values["lease"] == "owner-a"
    assert await manager.release("lease", "owner-a")
    assert "lease" not in redis.values


@pytest.mark.asyncio
async def test_lease_rejects_nonpositive_ttl_without_touching_redis() -> None:
    redis = MemoryRedis()
    manager = RedisLeaseManager(redis)

    with pytest.raises(ValueError, match="positive"):
        await manager.acquire("lease", "owner", ttl_seconds=0)

    assert redis.values == {}


@pytest.mark.asyncio
async def test_connectivity_probe_reports_outage_and_recovery() -> None:
    redis = MemoryRedis()
    probe = RedisConnectivityProbe(redis)

    redis.available = False
    unavailable = await probe.check()
    redis.available = True
    recovered = await probe.check()

    assert unavailable.available is False
    assert unavailable.error_key == "redis_unavailable"
    assert recovered.available is True
    assert recovered.error_key is None


@pytest.mark.asyncio
async def test_sandbox_quota_fails_closed_when_redis_is_unavailable() -> None:
    store = RedisSandboxQuotaStore(FailingRedis())

    assert isinstance(store, SandboxQuotaStorePort)
    assert not await store.acquire(
        node_id="node-a",
        holder_id="sandbox-a",
        node_limit=2,
        global_limit=10,
        holder_ttl_seconds=300,
    )


@pytest.mark.asyncio
async def test_sandbox_activity_records_explicit_ttl() -> None:
    redis = MemoryRedis()
    store = RedisSandboxActivityStore(redis)

    assert isinstance(store, SandboxActivityStorePort)
    assert await store.touch("sandbox-a", active_at_epoch=1234, ttl_seconds=86_400)
    assert redis.values["sandbox:last_active:sandbox-a"] == "1234"
    assert redis.expirations["sandbox:last_active:sandbox-a"] == ("ex", 86_400)
    assert await store.last_active("sandbox-a") == 1234


@pytest.mark.asyncio
async def test_wakeup_round_trip_and_read_degradation_are_explicit() -> None:
    redis = MemoryRedis()
    wakeup = RedisWakeupAdapter(redis)
    message = WakeupMessage(
        destination="execution.events",
        dedupe_key="event:1",
        event_position=7,
    )

    assert isinstance(wakeup, WakeupPort)
    await wakeup.publish(message)
    received = await wakeup.read("0-0", block_milliseconds=1)

    assert received.connectivity.available is True
    assert received.cursor == "1-0"
    assert received.messages == (message,)

    redis.available = False
    degraded = await wakeup.read(received.cursor, block_milliseconds=1)
    assert degraded.connectivity.available is False
    assert degraded.cursor == received.cursor
    assert degraded.messages == ()


def test_coordination_ports_are_runtime_checkable() -> None:
    assert isinstance(RedisSandboxQuotaStore(FailingRedis()), SandboxQuotaStorePort)
    assert isinstance(RedisSandboxActivityStore(FailingRedis()), SandboxActivityStorePort)
    assert isinstance(RedisLeaseManager(FailingRedis()), LeaseManagerPort)
    assert isinstance(object(), RateLimitStorePort) is False
