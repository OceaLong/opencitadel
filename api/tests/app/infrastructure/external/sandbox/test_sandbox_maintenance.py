import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.runtime_policy import RuntimePolicyStaleError, SandboxOperationsPolicy
from app.infrastructure.external.sandbox.sandbox_maintenance import SandboxMaintenance
from app.infrastructure.external.sandbox.settings import (
    SandboxDeployment,
    SandboxEffectiveSettings,
)


def _settings() -> SandboxEffectiveSettings:
    return SandboxEffectiveSettings(
        deployment=SandboxDeployment(
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
        ),
        operations_revision_id=uuid4(),
        policy=SandboxOperationsPolicy(),
    )


@pytest.mark.asyncio
async def test_stale_policy_blocks_maintenance_before_reconciliation() -> None:
    factory = SimpleNamespace(
        current_settings=AsyncMock(side_effect=RuntimePolicyStaleError(age_seconds=61)),
        pool=SimpleNamespace(reconcile=AsyncMock()),
        quota=SimpleNamespace(reconcile=AsyncMock()),
        list_live_sandbox_ids=AsyncMock(),
        cleanup_orphaned_containers=AsyncMock(),
    )
    maintenance = SandboxMaintenance(
        factory=factory,
        reclaim=SimpleNamespace(try_become_leader=AsyncMock(return_value=False)),
        activity_store=AsyncMock(),
    )

    with pytest.raises(RuntimePolicyStaleError):
        await maintenance.run_once()

    factory.current_settings.assert_awaited_once_with(require_fresh=True)
    factory.pool.reconcile.assert_not_awaited()
    factory.list_live_sandbox_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_maintenance_reconciles_pool_and_quota_with_one_exact_policy() -> None:
    settings = _settings()
    factory = SimpleNamespace(
        pool=SimpleNamespace(reconcile=AsyncMock()),
        quota=SimpleNamespace(reconcile=AsyncMock()),
        list_live_sandbox_ids=AsyncMock(return_value={"sandbox-1"}),
        cleanup_orphaned_containers=AsyncMock(),
    )
    reclaim = SimpleNamespace(try_become_leader=AsyncMock(return_value=False))
    maintenance = SandboxMaintenance(
        factory=factory,
        reclaim=reclaim,
        activity_store=AsyncMock(),
    )

    assert await maintenance.run_once(settings=settings) == 0

    factory.pool.reconcile.assert_awaited_once_with(settings)
    factory.list_live_sandbox_ids.assert_awaited_once_with(settings)
    factory.quota.reconcile.assert_awaited_once_with(
        {"sandbox-1"},
        settings.policy,
    )
    factory.cleanup_orphaned_containers.assert_not_awaited()
    reclaim.try_become_leader.assert_awaited_once_with(settings.policy.reclaim_leader_lease_seconds)


@pytest.mark.asyncio
async def test_maintenance_run_stops_cooperatively_without_owned_task() -> None:
    settings = _settings()
    factory = SimpleNamespace(current_settings=AsyncMock(return_value=settings))
    maintenance = SandboxMaintenance(
        factory=factory,
        reclaim=SimpleNamespace(),
        activity_store=AsyncMock(),
    )
    started = asyncio.Event()

    async def run_once(*, settings):
        del settings
        started.set()
        return 0

    maintenance.run_once = AsyncMock(side_effect=run_once)
    stopping = asyncio.Event()
    running = asyncio.create_task(maintenance.run(stopping))
    await asyncio.wait_for(started.wait(), timeout=1)
    stopping.set()
    await asyncio.wait_for(running, timeout=1)

    assert not hasattr(maintenance, "_task")
    assert not hasattr(maintenance, "start")
    assert not hasattr(maintenance, "shutdown")
