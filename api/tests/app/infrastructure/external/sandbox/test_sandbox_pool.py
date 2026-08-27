import asyncio
from unittest.mock import AsyncMock

import pytest

from app.domain.runtime_policy import OperationsPolicy, SandboxOperationsPolicy
from app.infrastructure.external.sandbox.factory import SandboxFactory
from app.infrastructure.external.sandbox.sandbox_pool import SandboxPool
from app.infrastructure.external.sandbox.settings import SandboxDeployment
from tests.runtime_policy_support import MutablePolicyReader


def _deployment() -> SandboxDeployment:
    return SandboxDeployment(
        driver="docker",
        address=None,
        image="sandbox:test",
        name_prefix="opencitadel-sandbox",
        network="isolated",
        chrome_args="",
        https_proxy=None,
        http_proxy=None,
        no_proxy=None,
        k8s_namespace="default",
        k8s_pod_label="app=opencitadel-sandbox",
    )


async def _settings(policy: SandboxOperationsPolicy):
    factory = SandboxFactory(
        deployment=_deployment(),
        operations=MutablePolicyReader(operations=OperationsPolicy(sandbox=policy)),
        quota_store=AsyncMock(),
        activity_store=AsyncMock(),
    )
    return await factory.current_settings(require_fresh=True)


class _Sandbox:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.id = "sandbox-test"
        self.destroy = AsyncMock(return_value=True)


class _Factory:
    def __init__(self, created) -> None:
        self.created = created
        self.create_unpooled = AsyncMock(return_value=created)


@pytest.mark.asyncio
async def test_pool_discards_old_policy_before_assignment() -> None:
    old = await _settings(SandboxOperationsPolicy(memory_limit="2g"))
    current = await _settings(SandboxOperationsPolicy(memory_limit="512m"))
    stale_sandbox = _Sandbox(old)
    current_sandbox = _Sandbox(current)
    factory = _Factory(current_sandbox)
    activity = AsyncMock()
    pool = SandboxPool(factory=factory, activity_store=activity)
    pool._queue.put_nowait(stale_sandbox)

    acquired = await pool.acquire(current)

    assert acquired is current_sandbox
    stale_sandbox.destroy.assert_awaited_once()
    factory.create_unpooled.assert_awaited_once_with(
        current,
        max_retries=current.policy.fast_warmup_max_retries,
    )
    activity.touch.assert_awaited_once()


@pytest.mark.asyncio
async def test_pool_reconcile_shrinks_excess_and_disabled_pool() -> None:
    enabled = await _settings(SandboxOperationsPolicy(pool_size=1))
    disabled = await _settings(SandboxOperationsPolicy(pool_enabled=False, pool_size=0))
    sandboxes = [_Sandbox(enabled) for _ in range(3)]
    pool = SandboxPool(factory=_Factory(_Sandbox(enabled)), activity_store=AsyncMock())
    for sandbox in sandboxes:
        pool._queue.put_nowait(sandbox)

    await pool.reconcile(enabled)
    assert pool._queue.qsize() == 1
    assert sum(item.destroy.await_count for item in sandboxes) == 2

    await pool.reconcile(disabled)
    assert pool._queue.empty()
    assert sum(item.destroy.await_count for item in sandboxes) == 3


@pytest.mark.asyncio
async def test_pool_run_stops_cooperatively_and_owns_no_background_task() -> None:
    current = await _settings(SandboxOperationsPolicy(pool_size=1))
    sandbox = _Sandbox(current)
    factory = _Factory(sandbox)
    factory.current_settings = AsyncMock(return_value=current)
    created = asyncio.Event()

    async def create_unpooled(_settings):
        created.set()
        return sandbox

    factory.create_unpooled = create_unpooled
    activity = AsyncMock()
    pool = SandboxPool(factory=factory, activity_store=activity)
    stopping = asyncio.Event()

    running = asyncio.create_task(pool.run(stopping))
    await asyncio.wait_for(created.wait(), timeout=1)
    stopping.set()
    await asyncio.wait_for(running, timeout=1)

    assert not hasattr(pool, "_warmup_task")
    assert not hasattr(pool, "start")
    assert not hasattr(pool, "shutdown")
    assert pool._queue.empty()
    sandbox.destroy.assert_awaited_once()
    activity.touch.assert_awaited_once()


@pytest.mark.asyncio
async def test_pool_cancellation_does_not_interrupt_owned_sandbox_drain() -> None:
    current = await _settings(SandboxOperationsPolicy(pool_size=2))
    first = _Sandbox(current)
    second = _Sandbox(current)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    reconcile_started = asyncio.Event()

    async def destroy_first() -> bool:
        first_started.set()
        await release_first.wait()
        return True

    first.destroy.side_effect = destroy_first
    factory = _Factory(first)

    async def current_settings(*, require_fresh: bool):
        assert require_fresh is True
        reconcile_started.set()
        return current

    factory.current_settings = AsyncMock(side_effect=current_settings)
    pool = SandboxPool(factory=factory, activity_store=AsyncMock())
    pool._queue.put_nowait(first)
    pool._queue.put_nowait(second)
    stopping = asyncio.Event()

    running = asyncio.create_task(pool.run(stopping))
    await asyncio.wait_for(reconcile_started.wait(), timeout=1)
    stopping.set()
    await asyncio.wait_for(first_started.wait(), timeout=1)
    running.cancel()
    await asyncio.sleep(0)
    release_first.set()

    with pytest.raises(asyncio.CancelledError):
        await running

    assert pool._queue.empty()
    first.destroy.assert_awaited_once()
    second.destroy.assert_awaited_once()
