"""Policy-reconciling pre-warmed Sandbox pool and activity tracking."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Protocol

from app.application.ports.coordination import SandboxActivityStorePort
from app.infrastructure.external.sandbox.settings import SandboxEffectiveSettings

if TYPE_CHECKING:
    from app.domain.external.sandbox import Sandbox

logger = logging.getLogger(__name__)

_SANDBOX_ACTIVITY_PREFIX = "sandbox:last_active:"
_SANDBOX_ACTIVITY_TTL_SECONDS = 86400


class _PoolFactory(Protocol):
    async def current_settings(
        self,
        *,
        require_fresh: bool,
    ) -> SandboxEffectiveSettings: ...

    async def create_unpooled(
        self,
        settings: SandboxEffectiveSettings,
        *,
        max_retries: int | None = None,
    ) -> Sandbox: ...


class SandboxPool:
    """A per-composition-root pool reconciled against current policy."""

    def __init__(
        self,
        *,
        factory: _PoolFactory,
        activity_store: SandboxActivityStorePort,
    ) -> None:
        self._factory = factory
        self._activity_store = activity_store
        self._queue: asyncio.Queue[Sandbox] = asyncio.Queue()
        self._lock = asyncio.Lock()

    @staticmethod
    def enabled(settings: SandboxEffectiveSettings) -> bool:
        return bool(
            not settings.deployment.address
            and settings.policy.pool_enabled
            and settings.policy.pool_size > 0
        )

    async def run(self, stopping: asyncio.Event) -> None:
        """Reconcile and warm the pool until the owning supervisor stops it."""

        logger.info("Sandbox pool policy reconciler started")
        try:
            while not stopping.is_set():
                wait_seconds = 1.0
                try:
                    settings = await self._factory.current_settings(require_fresh=True)
                    await self.reconcile(settings)
                    target = settings.policy.pool_size if self.enabled(settings) else 0
                    async with self._lock:
                        if self._queue.qsize() < target:
                            sandbox = await self._factory.create_unpooled(settings)
                            await self.touch_activity(sandbox.id)
                            await self._queue.put(sandbox)
                            logger.info(
                                "Sandbox pool warmed container=%s policy_revision=%s size=%s/%s",
                                sandbox.id,
                                settings.operations_revision_id,
                                self._queue.qsize(),
                                target,
                            )
                            continue
                    wait_seconds = min(
                        5.0,
                        float(settings.policy.cleanup_interval_seconds),
                    )
                except asyncio.CancelledError:
                    raise
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.warning("Sandbox pool reconciliation failed: %s", exc)
                await _wait_or_stop(stopping, wait_seconds)
        finally:
            drain_task = asyncio.create_task(
                self._drain_owned(),
                name="opencitadel:sandbox-pool-drain",
            )
            try:
                await asyncio.shield(drain_task)
            except asyncio.CancelledError:
                # The supervisor may cancel the reconciler immediately after
                # setting its cooperative stop event. Keep the owned cleanup
                # attached and wait for it before propagating cancellation.
                await drain_task
                raise

    async def acquire(self, settings: SandboxEffectiveSettings) -> Sandbox:
        async with self._lock:
            if not self.enabled(settings):
                sandbox = await self._factory.create_unpooled(settings)
                await self.touch_activity(sandbox.id)
                return sandbox

            while True:
                try:
                    sandbox = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    sandbox = await self._factory.create_unpooled(
                        settings,
                        max_retries=settings.policy.fast_warmup_max_retries,
                    )
                    await self.touch_activity(sandbox.id)
                    return sandbox
                if getattr(sandbox, "settings", None) != settings:
                    await sandbox.destroy()
                    continue
                await self._wipe_browser_profile(sandbox)
                await self.touch_activity(sandbox.id)
                return sandbox

    async def reconcile(self, settings: SandboxEffectiveSettings) -> None:
        """Drop stale/excess warm instances; the loop refills the new target."""
        async with self._lock:
            retained: list[Sandbox] = []
            target = settings.policy.pool_size if self.enabled(settings) else 0
            while not self._queue.empty():
                sandbox = self._queue.get_nowait()
                if getattr(sandbox, "settings", None) == settings and len(retained) < target:
                    retained.append(sandbox)
                else:
                    await sandbox.destroy()
            for sandbox in retained:
                self._queue.put_nowait(sandbox)

    async def _drain_owned(self) -> None:
        async with self._lock:
            await self._drain()

    async def _drain(self) -> None:
        sandboxes: list[Sandbox] = []
        while not self._queue.empty():
            sandboxes.append(self._queue.get_nowait())
        logger.info(
            "Sandbox pool draining count=%s containers=%s",
            len(sandboxes),
            [getattr(sandbox, "id", str(sandbox)) for sandbox in sandboxes],
        )
        results = await asyncio.gather(
            *(sandbox.destroy() for sandbox in sandboxes),
            return_exceptions=True,
        )
        for sandbox, result in zip(sandboxes, results, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    "Sandbox pool failed to destroy %s during drain: %s",
                    getattr(sandbox, "id", sandbox),
                    result,
                )
            elif not result:
                logger.error(
                    "Sandbox pool could not destroy %s during drain",
                    getattr(sandbox, "id", sandbox),
                )
        logger.info("Sandbox pool drain completed count=%s", len(sandboxes))

    @staticmethod
    async def _wipe_browser_profile(sandbox: Sandbox) -> None:
        exec_command = getattr(sandbox, "exec_command", None)
        if not callable(exec_command):
            return
        try:
            await sandbox.ensure_sandbox()
            result = await exec_command(
                "pool-reset",
                "/home/ubuntu",
                "rm -rf /home/ubuntu/.browser-profile",
            )
            if not result.success:
                logger.warning(
                    "Sandbox pool browser profile wipe failed for %s: %s",
                    getattr(sandbox, "id", sandbox),
                    result.message or result.data,
                )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(
                "Sandbox pool browser profile wipe failed for %s: %s",
                getattr(sandbox, "id", sandbox),
                exc,
            )

    async def touch_activity(self, container_name: str) -> None:
        if not container_name or container_name == "opencitadel-sandbox":
            return
        recorded = await self._activity_store.touch(
            container_name,
            active_at_epoch=int(time.time()),
            ttl_seconds=_SANDBOX_ACTIVITY_TTL_SECONDS,
        )
        if not recorded:
            logger.debug("Failed to record sandbox activity for %s", container_name)


async def _wait_or_stop(stopping: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stopping.wait(), timeout=max(0.0, seconds))
    except TimeoutError:
        return


__all__ = ["SandboxPool"]
