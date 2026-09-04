"""Capability-scoped Redis adapters built from one injected async client."""

from __future__ import annotations

import contextlib
import math
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.application.ports.coordination import (
    RateLimitDecision,
    RateLimitStorePort,
    RedisConnectivity,
)
from app.application.ports.execution import WakeupMessage
from app.application.ports.streams import (
    NOTIFICATION_HINT_CHANNEL_PREFIX,
    RUNTIME_POLICY_HINT_CHANNEL,
    SESSION_LIST_HINT_CHANNEL,
    HintPoll,
    WakeupBatch,
)

_REDIS_UNAVAILABLE = RedisConnectivity(False, "redis_unavailable")
_REDIS_AVAILABLE = RedisConnectivity(True, None)

_RENEW_LEASE_LUA = """
-- opencitadel:renew-lease
if redis.call("GET", KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call("PEXPIRE", KEYS[1], ARGV[2])
return 1
"""

_RELEASE_LEASE_LUA = """
-- opencitadel:release-lease
if redis.call("GET", KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call("DEL", KEYS[1])
return 1
"""

_ACQUIRE_SANDBOX_QUOTA_LUA = """
-- opencitadel:acquire-sandbox-quota
local node_inuse_key = KEYS[1]
local global_inuse_key = KEYS[2]
local holder_key = KEYS[3]
local node_capacity_key = KEYS[4]
local global_capacity_key = KEYS[5]
local node_limit = tonumber(ARGV[1])
local global_limit = tonumber(ARGV[2])
local holder_ttl = tonumber(ARGV[3])
local active_at = ARGV[4]
redis.call('SET', node_capacity_key, node_limit)
redis.call('SET', global_capacity_key, global_limit)
if redis.call('EXISTS', holder_key) == 1 then
    redis.call('EXPIRE', holder_key, holder_ttl)
    return 1
end
local node_inuse = tonumber(redis.call('GET', node_inuse_key) or '0')
local global_inuse = tonumber(redis.call('GET', global_inuse_key) or '0')
if node_inuse >= node_limit then
    return 0
end
if global_limit > 0 and global_inuse >= global_limit then
    return 0
end
redis.call('INCR', node_inuse_key)
redis.call('INCR', global_inuse_key)
redis.call('SET', holder_key, active_at, 'EX', holder_ttl)
return 1
"""

_RELEASE_SANDBOX_QUOTA_LUA = """
-- opencitadel:release-sandbox-quota
if redis.call('EXISTS', KEYS[3]) == 0 then
    return 0
end
redis.call('DEL', KEYS[3])
local node_inuse = tonumber(redis.call('GET', KEYS[1]) or '0')
local global_inuse = tonumber(redis.call('GET', KEYS[2]) or '0')
redis.call('SET', KEYS[1], math.max(0, node_inuse - 1))
redis.call('SET', KEYS[2], math.max(0, global_inuse - 1))
return 1
"""

_SLIDING_WINDOW_LUA = """
-- opencitadel:sliding-window-rate-limit
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_start = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local ttl = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', key, 0, window_start)
local count = redis.call('ZCARD', key)
if count >= limit then
  return 1
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, ttl)
return 0
"""

_HOLDER_PREFIX = "quota:sandbox:holder:"
_NODE_PREFIX = "quota:sandbox:node:"
_GLOBAL_INUSE_KEY = "quota:sandbox:global:inuse"
_GLOBAL_CAPACITY_KEY = "quota:sandbox:global:capacity"
_SANDBOX_ACTIVITY_PREFIX = "sandbox:last_active:"
_RATE_LIMIT_PREFIX = "ratelimit:"


def _ttl_milliseconds(ttl_seconds: float) -> int:
    if ttl_seconds <= 0:
        raise ValueError("lease TTL must be positive")
    return max(1, math.ceil(ttl_seconds * 1000))


def _positive_int(value: int, *, name: str) -> int:
    if isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


class RedisConnectivityProbe:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def check(self) -> RedisConnectivity:
        try:
            await self._redis.ping()
        except (OSError, RedisError, RuntimeError, ValueError):
            return _REDIS_UNAVAILABLE
        return _REDIS_AVAILABLE


class RedisLeaseManager:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def acquire(self, key: str, owner: str, *, ttl_seconds: float) -> bool:
        if not key or not owner:
            raise ValueError("lease key and owner are required")
        ttl_ms = _ttl_milliseconds(ttl_seconds)
        try:
            return bool(await self._redis.set(key, owner, nx=True, px=ttl_ms))
        except (OSError, RedisError, RuntimeError, ValueError):
            return False

    async def renew(self, key: str, owner: str, *, ttl_seconds: float) -> bool:
        if not key or not owner:
            raise ValueError("lease key and owner are required")
        ttl_ms = _ttl_milliseconds(ttl_seconds)
        try:
            renewed = await self._redis.eval(
                _RENEW_LEASE_LUA,
                1,
                key,
                owner,
                ttl_ms,
            )
            return int(renewed) == 1
        except (OSError, RedisError, RuntimeError, ValueError):
            return False

    async def release(self, key: str, owner: str) -> bool:
        if not key or not owner:
            raise ValueError("lease key and owner are required")
        try:
            released = await self._redis.eval(
                _RELEASE_LEASE_LUA,
                1,
                key,
                owner,
            )
            return int(released) == 1
        except (OSError, RedisError, RuntimeError, ValueError):
            return False


class RedisSandboxQuotaStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._connectivity = RedisConnectivityProbe(redis)

    @staticmethod
    def _node_inuse_key(node_id: str) -> str:
        return f"{_NODE_PREFIX}{node_id}:inuse"

    @staticmethod
    def _node_capacity_key(node_id: str) -> str:
        return f"{_NODE_PREFIX}{node_id}:capacity"

    @staticmethod
    def _holder_key(node_id: str, holder_id: str) -> str:
        return f"{_HOLDER_PREFIX}{node_id}:{holder_id}"

    async def available(self) -> RedisConnectivity:
        return await self._connectivity.check()

    async def can_admit(
        self,
        *,
        node_id: str,
        node_limit: int,
        global_limit: int,
    ) -> bool:
        if not node_id or node_limit <= 0 or global_limit < 0:
            return False
        try:
            await self._redis.set(self._node_capacity_key(node_id), node_limit)
            await self._redis.set(_GLOBAL_CAPACITY_KEY, global_limit)
            node_inuse = int(await self._redis.get(self._node_inuse_key(node_id)) or 0)
            if node_inuse >= node_limit:
                return False
            if global_limit > 0:
                global_inuse = int(await self._redis.get(_GLOBAL_INUSE_KEY) or 0)
                if global_inuse >= global_limit:
                    return False
            return True
        except (OSError, RedisError, RuntimeError, ValueError):
            return False

    async def acquire(
        self,
        *,
        node_id: str,
        holder_id: str,
        node_limit: int,
        global_limit: int,
        holder_ttl_seconds: int,
    ) -> bool:
        if (
            not node_id
            or not holder_id
            or node_limit <= 0
            or global_limit < 0
            or holder_ttl_seconds <= 0
        ):
            return False
        try:
            acquired = await self._redis.eval(
                _ACQUIRE_SANDBOX_QUOTA_LUA,
                5,
                self._node_inuse_key(node_id),
                _GLOBAL_INUSE_KEY,
                self._holder_key(node_id, holder_id),
                self._node_capacity_key(node_id),
                _GLOBAL_CAPACITY_KEY,
                node_limit,
                global_limit,
                holder_ttl_seconds,
                int(time.time()),
            )
            return int(acquired) == 1
        except (OSError, RedisError, RuntimeError, ValueError):
            return False

    async def release(self, *, node_id: str, holder_id: str) -> bool:
        if not node_id or not holder_id:
            return False
        try:
            released = await self._redis.eval(
                _RELEASE_SANDBOX_QUOTA_LUA,
                3,
                self._node_inuse_key(node_id),
                _GLOBAL_INUSE_KEY,
                self._holder_key(node_id, holder_id),
            )
            return int(released) == 1
        except (OSError, RedisError, RuntimeError, ValueError):
            return False

    async def heartbeat(
        self,
        *,
        node_id: str,
        holder_id: str,
        holder_ttl_seconds: int,
    ) -> bool:
        if not node_id or not holder_id or holder_ttl_seconds <= 0:
            return False
        try:
            holder_key = self._holder_key(node_id, holder_id)
            if not await self._redis.exists(holder_key):
                return False
            return bool(await self._redis.expire(holder_key, holder_ttl_seconds))
        except (OSError, RedisError, RuntimeError, ValueError):
            return False

    async def reconcile(
        self,
        *,
        node_id: str,
        live_holder_ids: set[str],
        node_limit: int,
        global_limit: int,
        holder_ttl_seconds: int,
    ) -> bool:
        if not node_id or node_limit <= 0 or global_limit < 0 or holder_ttl_seconds <= 0:
            return False
        try:
            await self._redis.set(self._node_capacity_key(node_id), node_limit)
            await self._redis.set(_GLOBAL_CAPACITY_KEY, global_limit)
            prefix = f"{_HOLDER_PREFIX}{node_id}:"
            known: set[str] = set()
            async for raw_key in self._redis.scan_iter(match=f"{prefix}*", count=100):
                key = _text(raw_key)
                holder_id = key.removeprefix(prefix)
                known.add(holder_id)
                if holder_id not in live_holder_ids:
                    await self.release(node_id=node_id, holder_id=holder_id)
            active_at = str(int(time.time()))
            for holder_id in live_holder_ids - known:
                await self._redis.set(
                    self._holder_key(node_id, holder_id),
                    active_at,
                    ex=holder_ttl_seconds,
                )
            await self._redis.set(self._node_inuse_key(node_id), len(live_holder_ids))
            total = 0
            async for _ in self._redis.scan_iter(match=f"{_HOLDER_PREFIX}*", count=200):
                total += 1
            await self._redis.set(_GLOBAL_INUSE_KEY, total)
            return True
        except (OSError, RedisError, RuntimeError, ValueError):
            return False

    async def node_in_use(self, node_id: str) -> int | None:
        if not node_id:
            return None
        try:
            return int(await self._redis.get(self._node_inuse_key(node_id)) or 0)
        except (OSError, RedisError, RuntimeError, ValueError):
            return None


class RedisSandboxActivityStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def touch(
        self,
        sandbox_id: str,
        *,
        active_at_epoch: int,
        ttl_seconds: int,
    ) -> bool:
        if not sandbox_id or ttl_seconds <= 0:
            return False
        try:
            return bool(
                await self._redis.set(
                    f"{_SANDBOX_ACTIVITY_PREFIX}{sandbox_id}",
                    str(active_at_epoch),
                    ex=ttl_seconds,
                )
            )
        except (OSError, RedisError, RuntimeError, ValueError):
            return False

    async def last_active(self, sandbox_id: str) -> int | None:
        if not sandbox_id:
            return None
        try:
            raw = await self._redis.get(f"{_SANDBOX_ACTIVITY_PREFIX}{sandbox_id}")
            return int(raw) if raw is not None else None
        except (OSError, RedisError, RuntimeError, ValueError):
            return None


class RedisWakeupAdapter:
    """Wake-up hints over one Redis stream, consumed via a consumer group.

    Thundering-herd治理 (K2-6/P2-5): with plain XREAD every kernel replica woke
    on every hint and stampeded the same PostgreSQL claim scan. A single fixed
    consumer group delivers each hint to exactly one consumer (replica), so one
    replica wakes and the rest keep sleeping on their block timeout. Hints are
    best-effort nudges over a durable PostgreSQL poll: entries are XACKed
    immediately after read, and pending entries of a crashed consumer are never
    XAUTOCLAIMed — a lost hint costs at most one idle-poll interval.
    """

    STREAM_KEY = "execution:wakeup"
    GROUP_NAME = "execution-kernel"

    def __init__(self, redis: Redis, *, consumer_name: str | None = None) -> None:
        self._redis = redis
        self._consumer_name = consumer_name or f"wakeup:{uuid.uuid4().hex[:12]}"
        self._group_ready = False

    async def publish(self, message: WakeupMessage) -> None:
        await self._redis.xadd(
            self.STREAM_KEY,
            {
                "destination": message.destination,
                "dedupe_key": message.dedupe_key,
                "event_position": str(message.event_position),
            },
        )

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            # id="0": the single kernel group also consumes hints published
            # before the group existed (first boot), instead of silently
            # skipping them until the durable poll catches up.
            await self._redis.xgroup_create(
                self.STREAM_KEY,
                self.GROUP_NAME,
                id="0",
                mkstream=True,
            )
        except RedisError as exc:
            # BUSYGROUP: the group already exists (another replica created it).
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def read(
        self,
        cursor: str,
        *,
        block_milliseconds: int,
    ) -> WakeupBatch:
        """Read a hint batch for this consumer; ``cursor`` is kept for API shape.

        The consumer group tracks delivery server-side, so the caller-visible
        cursor no longer advances; it is returned unchanged.
        """
        if block_milliseconds < 0:
            raise ValueError("block_milliseconds must not be negative")
        try:
            await self._ensure_group()
            streams = await self._redis.xreadgroup(
                self.GROUP_NAME,
                self._consumer_name,
                {self.STREAM_KEY: ">"},
                count=100,
                block=block_milliseconds,
            )
        except (OSError, RedisError, RuntimeError, ValueError):
            self._group_ready = False
            return WakeupBatch(cursor, (), _REDIS_UNAVAILABLE)
        messages, entry_ids = self._parse_entries(streams)
        if entry_ids:
            # Ack everything read (malformed entries included) so the pending
            # entries list never grows without bound. A failed ack is ignored:
            # un-acked hints are advisory and never re-claimed.
            with contextlib.suppress(OSError, RedisError, RuntimeError, ValueError):
                await self._redis.xack(self.STREAM_KEY, self.GROUP_NAME, *entry_ids)
        return WakeupBatch(cursor, tuple(messages), _REDIS_AVAILABLE)

    async def read_broadcast(
        self,
        cursor: str,
        *,
        block_milliseconds: int,
    ) -> WakeupBatch:
        """Broadcast read for SSE listeners: every listener sees every hint.

        Plain XREAD with a per-listener cursor — deliberately NOT the consumer
        group: a group read here would steal each hint from the kernel replicas
        (and from other listeners), degrading both back to their idle polls.
        The advanced cursor is returned for the caller to hold.
        """
        if block_milliseconds < 0:
            raise ValueError("block_milliseconds must not be negative")
        try:
            streams = await self._redis.xread(
                {self.STREAM_KEY: cursor},
                count=100,
                block=block_milliseconds,
            )
        except (OSError, RedisError, RuntimeError, ValueError):
            return WakeupBatch(cursor, (), _REDIS_UNAVAILABLE)
        messages, entry_ids = self._parse_entries(streams)
        next_cursor = entry_ids[-1] if entry_ids else cursor
        return WakeupBatch(next_cursor, tuple(messages), _REDIS_AVAILABLE)

    @staticmethod
    def _parse_entries(streams) -> tuple[list[WakeupMessage], list[str]]:
        messages: list[WakeupMessage] = []
        entry_ids: list[str] = []
        for _stream, entries in streams:
            for entry_id, raw in entries:
                entry_ids.append(_text(entry_id))
                fields = {_text(key): _text(value) for key, value in raw.items()}
                try:
                    messages.append(
                        WakeupMessage(
                            destination=fields["destination"],
                            dedupe_key=fields["dedupe_key"],
                            event_position=int(fields["event_position"]),
                        )
                    )
                except (KeyError, ValueError):
                    continue
        return messages, entry_ids


class RedisRateLimitStore(RateLimitStorePort):
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def check_and_record(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        _positive_int(limit, name="rate limit")
        _positive_int(window_seconds, name="rate limit window")
        now = time.time()
        try:
            result = await self._redis.eval(
                _SLIDING_WINDOW_LUA,
                1,
                f"{_RATE_LIMIT_PREFIX}{key}",
                now,
                now - window_seconds,
                limit,
                f"{now}:{uuid.uuid4().hex}",
                window_seconds + 5,
            )
        except (OSError, RedisError, RuntimeError, ValueError):
            return RateLimitDecision(False, _REDIS_UNAVAILABLE)
        return RateLimitDecision(int(result or 0) == 1, _REDIS_AVAILABLE)


class RedisHintPublisher:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, channel: str, payload: str) -> RedisConnectivity:
        try:
            await self._redis.publish(channel, payload)
        except (OSError, RedisError, RuntimeError, ValueError):
            return _REDIS_UNAVAILABLE
        return _REDIS_AVAILABLE


class _RedisHintStream:
    def __init__(self, pubsub: Any) -> None:
        self._pubsub = pubsub

    async def poll(self, *, timeout_seconds: float) -> HintPoll:
        try:
            message = await self._pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=timeout_seconds,
            )
        except (OSError, RedisError, RuntimeError, ValueError):
            return HintPoll(None, _REDIS_UNAVAILABLE)
        if not message or message.get("type") != "message":
            return HintPoll(None, _REDIS_AVAILABLE)
        return HintPoll(_text(message.get("data", "")), _REDIS_AVAILABLE)


class RedisHintStreamFactory:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @asynccontextmanager
    async def open(self, channel: str):
        pubsub = self._redis.pubsub()
        subscribed = False
        try:
            await pubsub.subscribe(channel)
            subscribed = True
            yield _RedisHintStream(pubsub)
        finally:
            try:
                if subscribed:
                    await pubsub.unsubscribe(channel)
            finally:
                await pubsub.aclose()


class RedisNotificationPublisher:
    def __init__(self, redis: Redis) -> None:
        self._publisher = RedisHintPublisher(redis)

    async def publish(self, user_id: str, payload: str) -> RedisConnectivity:
        return await self._publisher.publish(
            f"{NOTIFICATION_HINT_CHANNEL_PREFIX}{user_id}",
            payload,
        )


class RedisSessionListStreamFactory:
    def __init__(self, redis: Redis) -> None:
        self._streams = RedisHintStreamFactory(redis)

    def open(self):
        return self._streams.open(SESSION_LIST_HINT_CHANNEL)


class RedisNotificationStreamFactory:
    def __init__(self, redis: Redis) -> None:
        self._streams = RedisHintStreamFactory(redis)

    def open(self, user_id: str):
        return self._streams.open(f"{NOTIFICATION_HINT_CHANNEL_PREFIX}{user_id}")


class RedisRuntimePolicyHintStreamFactory:
    def __init__(self, redis: Redis) -> None:
        self._streams = RedisHintStreamFactory(redis)

    def open(self):
        return self._streams.open(RUNTIME_POLICY_HINT_CHANNEL)


__all__ = [
    "RedisConnectivityProbe",
    "RedisHintPublisher",
    "RedisHintStreamFactory",
    "RedisLeaseManager",
    "RedisNotificationPublisher",
    "RedisNotificationStreamFactory",
    "RedisRateLimitStore",
    "RedisRuntimePolicyHintStreamFactory",
    "RedisSandboxActivityStore",
    "RedisSandboxQuotaStore",
    "RedisSessionListStreamFactory",
    "RedisWakeupAdapter",
]
