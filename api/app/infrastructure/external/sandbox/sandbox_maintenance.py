"""Live-policy Sandbox pool, quota, idle, and memory reconciliation."""

from __future__ import annotations

import asyncio
import logging

from app.application.ports.coordination import SandboxActivityStorePort
from app.infrastructure.external.sandbox.factory import SandboxFactory
from app.infrastructure.external.sandbox.memory_probe import get_host_available_mb
from app.infrastructure.external.sandbox.reclaim_coordinator import (
    ReclaimCoordinator,
)
from app.infrastructure.external.sandbox.settings import SandboxEffectiveSettings

logger = logging.getLogger(__name__)


class SandboxMaintenance:
    def __init__(
        self,
        *,
        factory: SandboxFactory,
        reclaim: ReclaimCoordinator,
        activity_store: SandboxActivityStorePort,
    ) -> None:
        self._factory = factory
        self._reclaim = reclaim
        self._activity_store = activity_store

    async def run(self, stopping: asyncio.Event) -> None:
        """Reconcile sandbox lifecycle until the owning supervisor stops it."""

        while not stopping.is_set():
            wait_seconds = 1.0
            try:
                settings = await self._factory.current_settings(require_fresh=True)
                await self.run_once(settings=settings)
                wait_seconds = float(settings.policy.cleanup_interval_seconds)
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("Sandbox maintenance denied or failed: %s", exc)
            await _wait_or_stop(stopping, wait_seconds)

    async def run_once(
        self,
        *,
        settings: SandboxEffectiveSettings | None = None,
    ) -> int:
        """Reconcile one exact fresh policy decision."""
        current = settings or await self._factory.current_settings(require_fresh=True)
        await self._factory.pool.reconcile(current)
        live_ids = await self._factory.list_live_sandbox_ids(current)
        await self._factory.quota.reconcile(live_ids, current.policy)

        if not await self._reclaim.try_become_leader(current.policy.reclaim_leader_lease_seconds):
            return 0

        removed = await self._factory.cleanup_orphaned_containers(current)
        if removed:
            live_after_cleanup = await self._factory.list_live_sandbox_ids(current)
            await self._factory.quota.reconcile(
                live_after_cleanup,
                current.policy,
            )
        if not current.policy.admission_reclaim_enabled:
            return removed

        available = get_host_available_mb()
        if available is None or available >= current.policy.admission_min_host_available_mb:
            return removed

        extra = await self._reclaim_idle_for_memory(current)
        live_after = await self._factory.list_live_sandbox_ids(current)
        await self._factory.quota.reconcile(live_after, current.policy)
        return removed + extra

    async def _reclaim_idle_for_memory(
        self,
        settings: SandboxEffectiveSettings,
    ) -> int:
        candidates: list[tuple[int, str]] = []
        live = await self._factory.list_live_sandbox_ids(settings)
        for sandbox_id in live:
            last_active = await self._activity_store.last_active(sandbox_id)
            candidates.append((last_active or 0, sandbox_id))
        candidates.sort(key=lambda item: item[0])

        removed = 0
        policy = settings.policy
        for _, sandbox_id in candidates:
            available = get_host_available_mb()
            if available is not None and available >= policy.admission_reclaim_target_mb:
                break
            if (
                available is not None
                and available >= policy.admission_min_host_available_mb
                and removed > 0
            ):
                break
            sandbox = await self._factory.get(sandbox_id)
            if not sandbox:
                await self._factory.quota.release(sandbox_id)
                continue
            if await sandbox.destroy():
                removed += 1
                from app.infrastructure.observability.admission_metrics import (
                    record_sandbox_reclaimed,
                )

                record_sandbox_reclaimed("low_memory")
                logger.info(
                    "Low-memory reclaimed sandbox=%s policy_revision=%s",
                    sandbox_id,
                    settings.operations_revision_id,
                )
        return removed


async def _wait_or_stop(stopping: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stopping.wait(), timeout=max(0.0, seconds))
    except TimeoutError:
        return


__all__ = ["SandboxMaintenance"]
