"""Tests for LLM circuit breaker state machine and error classification."""

import asyncio
import time

from app.domain.runtime_policy import ModelResiliencePolicy
from app.domain.utils.llm_retry import is_breaker_eligible_error, is_retriable_llm_error
from app.infrastructure.external.llm.circuit_breaker import LLMCircuitBreaker


class TestBreakerErrorClassification:
    def test_retriable_429(self):
        assert is_retriable_llm_error(Exception("HTTP 429 rate limit"))

    def test_breaker_eligible_503(self):
        assert is_breaker_eligible_error(Exception("503 service unavailable"))

    def test_breaker_excludes_400(self):
        err = Exception("400 bad request invalid model")
        assert is_retriable_llm_error(err) is False or not is_breaker_eligible_error(err)

    def test_breaker_excludes_context_length(self):
        err = Exception("context_length_exceeded")
        assert not is_breaker_eligible_error(err)

    def test_breaker_includes_timeout(self):
        assert is_breaker_eligible_error(Exception("request timed out"))


async def _test_circuit_breaker_state_machine_open_halfopen_close():
    redis = _FakeRedisClient()
    breaker = LLMCircuitBreaker(redis)

    policy = _breaker_policy(threshold=2)

    assert await breaker.get_state("model-1", policy) == "closed"

    await breaker.record_failure("model-1", Exception("503 service unavailable"), policy)
    assert await breaker.get_state("model-1", policy) == "closed"

    await breaker.record_failure("model-1", Exception("503 service unavailable"), policy)
    assert await breaker.get_state("model-1", policy) == "open"
    assert await breaker.is_open("model-1", policy) is True
    assert await breaker.allow_request("model-1", policy) == "deny"
    assert await breaker.is_open("model-1", policy) is True

    redis.store[breaker._open_until_key("model-1")] = str(time.time() - 1)
    assert await breaker.get_state("model-1", policy) == "half_open"
    assert await breaker.allow_request("model-1", policy) == "probe"
    assert await breaker.allow_request("model-1", policy) == "deny"

    await breaker.record_success("model-1", policy)
    assert await breaker.get_state("model-1", policy) == "closed"
    assert await redis.get(breaker._open_until_key("model-1")) is None


def test_circuit_breaker_state_machine_open_halfopen_close():
    asyncio.run(_test_circuit_breaker_state_machine_open_halfopen_close())


async def _test_halfopen_probe_failure_reopens_circuit():
    redis = _FakeRedisClient()
    breaker = LLMCircuitBreaker(redis)

    policy = _breaker_policy(threshold=5)
    redis.store[breaker._open_until_key("model-1")] = str(time.time() - 1)

    assert await breaker.allow_request("model-1", policy) == "probe"
    assert breaker._probe_key("model-1") in redis.store
    await breaker.record_failure("model-1", Exception("503 service unavailable"), policy)

    assert await breaker.get_state("model-1", policy) == "open"
    assert await breaker.is_open("model-1", policy) is True
    assert breaker._probe_key("model-1") not in redis.store


def test_halfopen_probe_failure_reopens_circuit():
    asyncio.run(_test_halfopen_probe_failure_reopens_circuit())


def _breaker_policy(*, threshold: int) -> ModelResiliencePolicy:
    return ModelResiliencePolicy(breaker_error_threshold=threshold)


class _FakeRedisClient:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.zsets: dict[str, list[float]] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def delete(self, *keys: str):
        deleted = 0
        for key in keys:
            deleted += int(key in self.store or key in self.zsets)
            self.store.pop(key, None)
            self.zsets.pop(key, None)
        return deleted

    async def eval(self, _script: str, numkeys: int, *args):
        if numkeys == 2:
            return self._allow_eval(*args)
        if numkeys == 3:
            return self._record_error_eval(*args)
        raise AssertionError(f"unexpected eval numkeys={numkeys}")

    def _allow_eval(self, open_until_key: str, probe_key: str, now: str, probe_ttl: str):
        del probe_ttl
        current = float(now)
        open_until = float(self.store.get(open_until_key) or 0)
        if open_until > current:
            return "deny"
        if open_until > 0:
            if probe_key not in self.store:
                self.store[probe_key] = "1"
                return "probe"
            return "deny"
        return "allow"

    def _record_error_eval(
        self,
        errors_key: str,
        open_until_key: str,
        probe_key: str,
        now: str,
        window: str,
        threshold: str,
        open_ttl: str,
        key_ttl: str,
    ):
        del key_ttl
        current = float(now)
        window_seconds = float(window)
        threshold_count = int(threshold)
        open_ttl_seconds = float(open_ttl)
        zset = [
            score for score in self.zsets.get(errors_key, []) if score >= current - window_seconds
        ]
        zset.append(current)
        self.zsets[errors_key] = zset
        open_until = float(self.store.get(open_until_key) or 0)
        if open_until > 0 and current >= open_until:
            self.store[open_until_key] = str(current + open_ttl_seconds)
            self.store.pop(probe_key, None)
            return "open"
        if len(zset) >= threshold_count:
            self.store[open_until_key] = str(current + open_ttl_seconds)
            return "open"
        return "closed"
