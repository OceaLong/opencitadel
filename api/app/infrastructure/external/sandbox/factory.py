"""Policy-aware Sandbox factory without module-global behavior state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.application.ports.coordination import (
    SandboxActivityStorePort,
    SandboxQuotaStorePort,
)
from app.application.services.runtime_policy_reader import OperationsPolicyReader
from app.domain.external.sandbox import Sandbox
from app.domain.models.scope import OwnerScope
from app.domain.utils.time_utils import utc_now
from app.infrastructure.external.sandbox.admission import SandboxQuota
from app.infrastructure.external.sandbox.sandbox_driver import get_sandbox_class
from app.infrastructure.external.sandbox.sandbox_pool import SandboxPool
from app.infrastructure.external.sandbox.settings import (
    SandboxDeployment,
    SandboxEffectiveSettings,
    SandboxHostAccess,
)
from core.config import DeploymentSettings


class SandboxFactory:
    """Attach/inspect surface shared by both process roles (D14/P2-16②).

    This base class deliberately has no warm pool and cannot create sandboxes:
    ``PooledSandboxFactory`` (execution-kernel process) adds the pool-backed
    ``create``, while ``AttachOnlySandboxFactory`` (API process) fails loudly
    on ``create`` so the API can never silently cold-start a sandbox.
    """

    def __init__(
        self,
        *,
        deployment: SandboxDeployment,
        operations: OperationsPolicyReader,
        quota_store: SandboxQuotaStorePort,
        activity_store: SandboxActivityStorePort,
        host: SandboxHostAccess | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._deployment = deployment
        self._operations = operations
        self._host = host or SandboxHostAccess(
            environment="development",
            broker_url=None,
            broker_token=None,
            redis_host="localhost",
            redis_port=6379,
            redis_db=0,
            redis_password=None,
        )
        self._quota = SandboxQuota(deployment=deployment, store=quota_store)
        self._clock = clock

    @classmethod
    def from_settings(
        cls,
        *,
        settings: DeploymentSettings,
        operations: OperationsPolicyReader,
        quota_store: SandboxQuotaStorePort,
        activity_store: SandboxActivityStorePort,
        clock: Callable[[], datetime] = utc_now,
    ) -> SandboxFactory:
        return cls(
            deployment=SandboxDeployment.from_settings(settings),
            host=SandboxHostAccess.from_settings(settings),
            operations=operations,
            quota_store=quota_store,
            activity_store=activity_store,
            clock=clock,
        )

    @property
    def deployment(self) -> SandboxDeployment:
        return self._deployment

    @property
    def quota(self) -> SandboxQuota:
        return self._quota

    async def create(self, *, owner_scope: OwnerScope) -> Sandbox:
        raise NotImplementedError(
            "this SandboxFactory cannot create sandboxes; use PooledSandboxFactory"
        )

    async def current_settings(
        self,
        *,
        require_fresh: bool,
    ) -> SandboxEffectiveSettings:
        active = await self._operations.active_operations(
            require_fresh=require_fresh,
            now=self._clock(),
        )
        return SandboxEffectiveSettings(
            deployment=self._deployment,
            operations_revision_id=active.revision.id,
            policy=active.revision.policy.sandbox,
        )

    async def create_unpooled(
        self,
        settings: SandboxEffectiveSettings,
        *,
        max_retries: int | None = None,
    ) -> Sandbox:
        driver = get_sandbox_class(settings.deployment)
        return await driver.create_and_warm(
            settings,
            self._host,
            self._quota,
            max_retries=max_retries,
        )

    async def get(self, sandbox_id: str) -> Sandbox | None:
        settings = await self.current_settings(require_fresh=False)
        driver = get_sandbox_class(settings.deployment)
        return await driver.get(
            settings,
            self._host,
            self._quota,
            sandbox_id,
        )

    async def list_live_sandbox_ids(
        self,
        settings: SandboxEffectiveSettings,
    ) -> set[str]:
        driver = get_sandbox_class(settings.deployment)
        if settings.deployment.driver == "kubernetes":
            return await driver.list_live_sandbox_ids(settings)
        return await driver.list_live_sandbox_ids(settings, self._host)

    async def cleanup_orphaned_containers(
        self,
        settings: SandboxEffectiveSettings,
    ) -> int:
        driver = get_sandbox_class(settings.deployment)
        return await driver.cleanup_orphaned_containers(settings, self._host)


class PooledSandboxFactory(SandboxFactory):
    """Kernel-process factory: owns the warm pool and may create sandboxes."""

    def __init__(
        self,
        *,
        deployment: SandboxDeployment,
        operations: OperationsPolicyReader,
        quota_store: SandboxQuotaStorePort,
        activity_store: SandboxActivityStorePort,
        host: SandboxHostAccess | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        super().__init__(
            deployment=deployment,
            operations=operations,
            quota_store=quota_store,
            activity_store=activity_store,
            host=host,
            clock=clock,
        )
        self._pool = SandboxPool(factory=self, activity_store=activity_store)

    @property
    def pool(self) -> SandboxPool:
        return self._pool

    async def create(self, *, owner_scope: OwnerScope) -> Sandbox:
        settings = await self.current_settings(require_fresh=True)
        sandbox = await self._pool.acquire(settings)
        sandbox.owner_scope = owner_scope
        return sandbox


class AttachOnlySandboxFactory(SandboxFactory):
    """API-process factory: attaches to existing sandboxes, never creates one.

    The execution kernel is the sole sandbox creator (D14/P2-16②); an API
    request path reaching ``create`` is a wiring bug and fails loudly instead
    of silently cold-starting a container in the wrong process.
    """

    async def create(self, *, owner_scope: OwnerScope) -> Sandbox:
        raise RuntimeError(
            "sandbox creation is execution-kernel-only; the API process may "
            "only attach to existing sandboxes"
        )


__all__ = ["AttachOnlySandboxFactory", "PooledSandboxFactory", "SandboxFactory"]
