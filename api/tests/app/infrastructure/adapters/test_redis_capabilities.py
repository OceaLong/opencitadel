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
from app.application.ports.streams import WakeupBroadcastPort, WakeupPort
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
        # stream -> group -> {"delivered": int, "pending": {entry_id: consumer}}
        self.groups: dict[str, dict[str, dict[str, Any]]] = {}
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

    async def xgroup_create(
        self,
        stream: str,
        group: str,
        *,
        id: str = "$",
        mkstream: bool = False,
    ) -> bool:
        await self.ping()
        if mkstream:
            self.streams.setdefault(stream, [])
        groups = self.groups.setdefault(stream, {})
        if group in groups:
            from redis.exceptions import ResponseError

            raise ResponseError("BUSYGROUP Consumer Group name already exists")
        delivered = len(self.streams.get(stream, [])) if id == "$" else 0
        groups[group] = {"delivered": delivered, "pending": {}}
        return True

    async def xreadgroup(
        self,
        group: str,
        consumer: str,
        streams: dict[str, str],
        *,
        count: int,
        block: int,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        del block
        await self.ping()
        stream, cursor = next(iter(streams.items()))
        assert cursor == ">"
        state = self.groups[stream][group]
        entries = self.streams.get(stream, [])[state["delivered"] :][:count]
        state["delivered"] += len(entries)
        for entry_id, _values in entries:
            state["pending"][entry_id] = consumer
        return [(stream, entries)] if entries else []

    async def xack(self, stream: str, group: str, *entry_ids: str) -> int:
        await self.ping()
        state = self.groups[stream][group]
        acked = 0
        for entry_id in entry_ids:
            acked += int(state["pending"].pop(entry_id, None) is not None)
        return acked

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
    received = await wakeup.read("$", block_milliseconds=1)

    assert received.connectivity.available is True
    # Consumer-group delivery is tracked server-side; the API-shape cursor is
    # returned unchanged.
    assert received.cursor == "$"
    assert received.messages == (message,)
    # Delivered entries are acked immediately so the PEL never grows.
    assert (
        redis.groups[RedisWakeupAdapter.STREAM_KEY][RedisWakeupAdapter.GROUP_NAME]["pending"] == {}
    )

    redis.available = False
    degraded = await wakeup.read(received.cursor, block_milliseconds=1)
    assert degraded.connectivity.available is False
    assert degraded.cursor == received.cursor
    assert degraded.messages == ()


@pytest.mark.asyncio
async def test_wakeup_consumer_group_delivers_each_hint_to_exactly_one_replica() -> None:
    """K2-6 惊群治理: two kernel replicas share one consumer group, so a hint
    wakes exactly one of them instead of stampeding both."""
    redis = MemoryRedis()
    replica_a = RedisWakeupAdapter(redis, consumer_name="replica-a")
    replica_b = RedisWakeupAdapter(redis, consumer_name="replica-b")
    message = WakeupMessage(
        destination="execution.events",
        dedupe_key="event:2",
        event_position=8,
    )

    await replica_a.publish(message)
    first = await replica_a.read("$", block_milliseconds=1)
    second = await replica_b.read("$", block_milliseconds=1)

    assert first.messages == (message,)
    assert second.messages == ()  # already delivered to (and acked by) replica-a


def test_coordination_ports_are_runtime_checkable() -> None:
    assert isinstance(RedisSandboxQuotaStore(FailingRedis()), SandboxQuotaStorePort)
    assert isinstance(RedisSandboxActivityStore(FailingRedis()), SandboxActivityStorePort)
    assert isinstance(RedisLeaseManager(FailingRedis()), LeaseManagerPort)
    assert isinstance(object(), RateLimitStorePort) is False


@pytest.mark.asyncio
async def test_wakeup_broadcast_reaches_every_listener_and_never_steals_from_the_group() -> None:
    """SSE listeners consume in broadcast mode (own cursor each): every
    listener sees every hint, and none of them consumes the kernel consumer
    group's delivery — a group read from an SSE stream would silently steal
    hints from the kernel replicas."""
    redis = MemoryRedis()
    kernel = RedisWakeupAdapter(redis, consumer_name="replica-a")
    listener_a = RedisWakeupAdapter(redis)
    listener_b = RedisWakeupAdapter(redis)
    message = WakeupMessage(
        destination="execution.events",
        dedupe_key="event:3",
        event_position=9,
    )
    assert isinstance(listener_a, WakeupBroadcastPort)

    await kernel.publish(message)
    seen_a = await listener_a.read_broadcast("0", block_milliseconds=1)
    seen_b = await listener_b.read_broadcast("0", block_milliseconds=1)

    # Broadcast: both listeners observe the hint, cursors advance.
    assert seen_a.messages == (message,)
    assert seen_b.messages == (message,)
    assert seen_a.cursor != "0"

    # The kernel's group delivery is untouched by the broadcast reads.
    delivered = await kernel.read("$", block_milliseconds=1)
    assert delivered.messages == (message,)


@pytest.mark.asyncio
async def test_wakeup_broadcast_degrades_explicitly_when_redis_is_unavailable() -> None:
    redis = MemoryRedis()
    listener = RedisWakeupAdapter(redis)
    redis.available = False

    degraded = await listener.read_broadcast("0", block_milliseconds=1)

    assert degraded.connectivity.available is False
    assert degraded.messages == ()
    assert degraded.cursor == "0"
