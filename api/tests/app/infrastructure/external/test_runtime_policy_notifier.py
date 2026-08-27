import asyncio

import pytest
from redis.asyncio import Redis as AsyncRedis

from core.config import load_deployment_settings


class _Redis:
    def __init__(self, *, publish_error: Exception | None = None) -> None:
        self.publish_error = publish_error
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        if self.publish_error is not None:
            raise self.publish_error
        self.published.append((channel, payload))


@pytest.mark.asyncio
async def test_policy_hint_contains_only_committed_head_version() -> None:
    from app.infrastructure.external.runtime_policy_notifier import (
        RUNTIME_POLICY_CHANGED_CHANNEL,
        RuntimePolicyHintPublisher,
    )

    redis = _Redis()
    publisher = RuntimePolicyHintPublisher(redis=redis)

    await publisher.publish_changed(42)

    assert redis.published == [(RUNTIME_POLICY_CHANGED_CHANNEL, "42")]


@pytest.mark.asyncio
async def test_policy_hint_failure_does_not_change_committed_result() -> None:
    from app.infrastructure.external.runtime_policy_notifier import (
        RuntimePolicyHintPublisher,
    )

    publisher = RuntimePolicyHintPublisher(redis=_Redis(publish_error=OSError("redis unavailable")))
    await publisher.publish_changed(42)


def test_policy_hint_rejects_invalid_head_version() -> None:
    from app.infrastructure.external.runtime_policy_notifier import (
        RuntimePolicyHintPublisher,
    )

    with pytest.raises(ValueError, match="positive integer"):
        asyncio.run(RuntimePolicyHintPublisher(redis=_Redis()).publish_changed(0))


@pytest.mark.asyncio
async def test_real_redis_hint_refreshes_reader(redis_integration) -> None:
    del redis_integration
    from app.infrastructure.adapters.redis_capabilities import (
        RedisRuntimePolicyHintStreamFactory,
    )
    from app.infrastructure.external.runtime_policy_notifier import (
        RUNTIME_POLICY_CHANGED_CHANNEL,
        RuntimePolicyHintListener,
    )

    refreshed = asyncio.Event()

    class _Reader:
        async def handle_hint(self) -> None:
            refreshed.set()

    settings = load_deployment_settings()
    redis = AsyncRedis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password,
        decode_responses=True,
    )
    listener = RuntimePolicyHintListener(
        streams=RedisRuntimePolicyHintStreamFactory(redis),
        reader=_Reader(),
    )
    running = asyncio.create_task(listener.run())
    try:
        await asyncio.sleep(0)
        await redis.publish(RUNTIME_POLICY_CHANGED_CHANNEL, "2")
        await asyncio.wait_for(refreshed.wait(), timeout=2)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await redis.aclose()
