"""Redis-backed distributed circuit breaker for LLM providers."""

from __future__ import annotations

import logging
import time
from enum import StrEnum
from typing import Any

from redis.asyncio import Redis

from app.domain.runtime_policy import ModelResiliencePolicy
from app.domain.utils.llm_retry import is_breaker_eligible_error

logger = logging.getLogger(__name__)

_OPEN_UNTIL_PREFIX = "cb:open_until:"
_ERRORS_PREFIX = "cb:errors:"
_PROBE_PREFIX = "cb:probe:"


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


_RECORD_ERROR_LUA = """
local errors_key = KEYS[1]
local open_until_key = KEYS[2]
local probe_key = KEYS[3]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local threshold = tonumber(ARGV[3])
local open_ttl = tonumber(ARGV[4])
local key_ttl = tonumber(ARGV[5])
local open_until = tonumber(redis.call('GET', open_until_key) or '0')
redis.call('ZREMRANGEBYSCORE', errors_key, '-inf', now - window)
redis.call('ZADD', errors_key, now, now .. ':' .. math.random(1000000))
redis.call('EXPIRE', errors_key, window + 60)
local count = redis.call('ZCARD', errors_key)
if open_until > 0 and now >= open_until then
  redis.call('SET', open_until_key, now + open_ttl, 'EX', key_ttl)
  redis.call('DEL', probe_key)
  return 'open'
end
if count >= threshold then
  redis.call('SET', open_until_key, now + open_ttl, 'EX', key_ttl)
  return 'open'
end
return 'closed'
"""

_ALLOW_REQUEST_LUA = """
local open_until_key = KEYS[1]
local probe_key = KEYS[2]
local now = tonumber(ARGV[1])
local probe_ttl = tonumber(ARGV[2])
local open_until = tonumber(redis.call('GET', open_until_key) or '0')
if open_until > now then
  return 'deny'
end
if open_until > 0 then
  if redis.call('SET', probe_key, '1', 'NX', 'EX', probe_ttl) then
    return 'probe'
  end
  return 'deny'
end
return 'allow'
"""


class LLMCircuitBreaker:
    """Per-model_id circuit breaker with Redis Lua atomicity."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @staticmethod
    def _open_until_key(model_id: str) -> str:
        return f"{_OPEN_UNTIL_PREFIX}{model_id}"

    @staticmethod
    def _errors_key(model_id: str) -> str:
        return f"{_ERRORS_PREFIX}{model_id}"

    @staticmethod
    def _probe_key(model_id: str) -> str:
        return f"{_PROBE_PREFIX}{model_id}"

    async def get_state(
        self,
        model_id: str,
        policy: ModelResiliencePolicy,
    ) -> BreakerState:
        if not model_id:
            return BreakerState.CLOSED
        if not policy.enabled:
            return BreakerState.CLOSED
        try:
            open_until = await self._get_open_until(model_id)
            if open_until <= 0:
                return BreakerState.CLOSED
            if open_until > time.time():
                return BreakerState.OPEN
            return BreakerState.HALF_OPEN
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("熔断状态读取失败 (fail-open): model_id=%s error=%s", model_id, exc)
            return BreakerState.CLOSED

    async def is_open(self, model_id: str, policy: ModelResiliencePolicy) -> bool:
        if not model_id or not policy.enabled:
            return False
        try:
            return await self._get_open_until(model_id) > time.time()
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("熔断开路查询失败 (fail-open): model_id=%s error=%s", model_id, exc)
            return False

    async def allow_request(self, model_id: str, policy: ModelResiliencePolicy) -> str:
        """Return allow/probe/deny for closed, half-open probe, and open states."""
        if not model_id or not policy.enabled:
            return "allow"
        try:
            result = await self._redis.eval(
                _ALLOW_REQUEST_LUA,
                2,
                self._open_until_key(model_id),
                self._probe_key(model_id),
                str(time.time()),
                str(policy.breaker_halfopen_probe_timeout_seconds),
            )
            return result.decode() if isinstance(result, bytes) else str(result)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("熔断放行判定失败 (fail-open): model_id=%s error=%s", model_id, exc)
            return "allow"

    async def record_success(self, model_id: str, policy: ModelResiliencePolicy) -> None:
        if not model_id or not policy.enabled:
            return
        try:
            await self._redis.delete(
                self._open_until_key(model_id),
                self._errors_key(model_id),
                self._probe_key(model_id),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("熔断成功记录失败: model_id=%s error=%s", model_id, exc)

    async def record_failure(
        self,
        model_id: str,
        error: Exception,
        policy: ModelResiliencePolicy,
    ) -> None:
        if not model_id or not is_breaker_eligible_error(error):
            return
        if not policy.enabled:
            return
        try:
            now = time.time()
            result = await self._redis.eval(
                _RECORD_ERROR_LUA,
                3,
                self._errors_key(model_id),
                self._open_until_key(model_id),
                self._probe_key(model_id),
                str(now),
                str(policy.breaker_window_seconds),
                str(policy.breaker_error_threshold),
                str(policy.breaker_open_ttl_seconds),
                str(policy.breaker_open_ttl_seconds + policy.breaker_window_seconds + 60),
            )
            if result == b"open" or result == "open":
                logger.warning("熔断器开路: model_id=%s", model_id)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("熔断失败记录失败 (fail-open): model_id=%s error=%s", model_id, exc)

    async def snapshot(
        self,
        model_id: str,
        policy: ModelResiliencePolicy,
    ) -> dict[str, Any]:
        state = await self.get_state(model_id, policy)
        return {"model_id": model_id, "state": state.value}

    async def _get_open_until(self, model_id: str) -> float:
        raw = await self._redis.get(self._open_until_key(model_id))
        if not raw:
            return 0.0
        value = raw.decode() if isinstance(raw, bytes) else str(raw)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
