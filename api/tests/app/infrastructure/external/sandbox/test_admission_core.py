from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.runtime_policy import SandboxOperationsPolicy
from app.infrastructure.external.sandbox.admission import SandboxQuota
from app.infrastructure.external.sandbox.driver_resolve import resolve_sandbox_driver
from app.infrastructure.external.sandbox.node_id import resolve_node_id
from app.infrastructure.external.sandbox.settings import SandboxDeployment


def test_resolve_sandbox_driver_explicit():
    assert resolve_sandbox_driver("docker") == "docker"
    assert resolve_sandbox_driver("kubernetes") == "kubernetes"


def _deployment(driver):
    return SandboxDeployment(
        driver=driver,
        address=None,
        image="sandbox:test",
        name_prefix="opencitadel-sandbox",
        network=None,
        chrome_args="",
        https_proxy=None,
        http_proxy=None,
        no_proxy=None,
        k8s_namespace="default",
        k8s_pod_label="app=opencitadel-sandbox",
    )


def test_admission_memory_probe_is_bound_to_injected_driver():
    store = AsyncMock()
    docker = SandboxQuota(
        deployment=_deployment("docker"),
        store=store,
        node_id="docker-node",
    )
    kubernetes = SandboxQuota(
        deployment=_deployment("kubernetes"),
        store=store,
        node_id="kubernetes-node",
    )

    assert docker._should_check_memory() is True
    assert kubernetes._should_check_memory() is False


def test_resolve_node_id_returns_string():
    assert resolve_node_id()


@pytest.mark.asyncio
async def test_real_redis_quota_applies_live_tightening(
    redis_integration,
    monkeypatch,
) -> None:
    del redis_integration
    from redis.asyncio import Redis

    from app.infrastructure.adapters.redis_capabilities import RedisSandboxQuotaStore
    from app.infrastructure.external.sandbox import admission
    from core.config import load_deployment_settings

    monkeypatch.setattr(admission, "memory_meets_threshold", lambda _minimum: True)
    settings = load_deployment_settings()
    redis_client = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password,
        decode_responses=True,
    )
    store = RedisSandboxQuotaStore(redis_client)
    quota = SandboxQuota(
        deployment=_deployment("docker"),
        store=store,
        node_id=f"test-{uuid4().hex}",
    )
    first_holder = f"sandbox-{uuid4().hex}"
    second_holder = f"sandbox-{uuid4().hex}"
    permissive = SandboxOperationsPolicy(
        max_sandboxes_per_node=2,
        admission_settle_seconds=0,
    )
    tightened = permissive.model_copy(update={"max_sandboxes_per_node": 1})

    try:
        assert await quota.acquire(first_holder, permissive) is True
        assert await quota.acquire(second_holder, tightened) is False
    finally:
        await quota.release(first_holder)
        await quota.release(second_holder)
        await redis_client.delete(
            store._node_inuse_key(quota.node_id),
            store._node_capacity_key(quota.node_id),
        )
        await redis_client.aclose()
