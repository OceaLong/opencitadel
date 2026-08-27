"""Policy-aware sandbox admission over an injected quota capability."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from app.application.ports.coordination import SandboxQuotaStorePort
from app.domain.runtime_policy import SandboxOperationsPolicy
from app.infrastructure.external.sandbox.memory_probe import memory_meets_threshold
from app.infrastructure.external.sandbox.node_id import resolve_node_id
from app.infrastructure.external.sandbox.settings import SandboxDeployment
from app.infrastructure.observability.admission_metrics import (
    record_admission_rejected,
    set_quota_inuse,
)

_HOLDER_TTL_SECONDS = 300


class AdmissionPolicy(ABC):
    @abstractmethod
    async def can_admit(self, policy: SandboxOperationsPolicy) -> bool: ...

    @abstractmethod
    async def acquire(self, holder_id: str, policy: SandboxOperationsPolicy) -> bool: ...

    @abstractmethod
    async def release(self, holder_id: str) -> None: ...

    @abstractmethod
    async def heartbeat(self, holder_id: str) -> None: ...

    @abstractmethod
    async def reconcile(
        self,
        live_holder_ids: set[str],
        policy: SandboxOperationsPolicy,
    ) -> None: ...


class SandboxQuota(AdmissionPolicy):
    """Host-memory policy plus Redis-backed distributed quota state."""

    def __init__(
        self,
        *,
        deployment: SandboxDeployment,
        store: SandboxQuotaStorePort,
        node_id: str | None = None,
    ) -> None:
        self._deployment = deployment
        self._store = store
        self._node_id = node_id or resolve_node_id()

    @property
    def node_id(self) -> str:
        return self._node_id

    def _should_check_memory(self) -> bool:
        return self._deployment.driver != "kubernetes"

    async def _redis_ready(self) -> bool:
        connectivity = await self._store.available()
        if connectivity.available:
            return True
        record_admission_rejected("redis_unavailable")
        return False

    def _memory_ready(self, policy: SandboxOperationsPolicy) -> bool:
        if not self._should_check_memory() or memory_meets_threshold(
            policy.admission_min_host_available_mb
        ):
            return True
        record_admission_rejected("memory_low")
        return False

    async def _publish_quota_metrics(self) -> None:
        in_use = await self._store.node_in_use(self._node_id)
        if in_use is not None:
            set_quota_inuse(self._node_id, in_use)

    async def can_admit(self, policy: SandboxOperationsPolicy) -> bool:
        if not await self._redis_ready() or not self._memory_ready(policy):
            return False
        return await self._store.can_admit(
            node_id=self._node_id,
            node_limit=policy.max_sandboxes_per_node,
            global_limit=policy.max_dynamic_sandboxes_global,
        )

    async def acquire(self, holder_id: str, policy: SandboxOperationsPolicy) -> bool:
        if not holder_id:
            return False
        if not await self._redis_ready() or not self._memory_ready(policy):
            return False
        acquired = await self._store.acquire(
            node_id=self._node_id,
            holder_id=holder_id,
            node_limit=policy.max_sandboxes_per_node,
            global_limit=policy.max_dynamic_sandboxes_global,
            holder_ttl_seconds=_HOLDER_TTL_SECONDS,
        )
        if not acquired:
            return False
        if policy.admission_settle_seconds > 0:
            await asyncio.sleep(policy.admission_settle_seconds)
        await self._publish_quota_metrics()
        return True

    async def release(self, holder_id: str) -> None:
        if not holder_id:
            return
        await self._store.release(node_id=self._node_id, holder_id=holder_id)
        await self._publish_quota_metrics()

    async def heartbeat(self, holder_id: str) -> None:
        if not holder_id:
            return
        await self._store.heartbeat(
            node_id=self._node_id,
            holder_id=holder_id,
            holder_ttl_seconds=_HOLDER_TTL_SECONDS,
        )

    async def reconcile(
        self,
        live_holder_ids: set[str],
        policy: SandboxOperationsPolicy,
    ) -> None:
        reconciled = await self._store.reconcile(
            node_id=self._node_id,
            live_holder_ids=live_holder_ids,
            node_limit=policy.max_sandboxes_per_node,
            global_limit=policy.max_dynamic_sandboxes_global,
            holder_ttl_seconds=_HOLDER_TTL_SECONDS,
        )
        if reconciled:
            await self._publish_quota_metrics()


__all__ = ["AdmissionPolicy", "SandboxQuota"]
