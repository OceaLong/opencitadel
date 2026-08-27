"""Application boundary for bounded immutable resource-version retention."""

import inspect
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.services.runtime_policy_reader import OperationsPolicyReader
from app.domain.repositories.codebase_version_repository import (
    CodebaseVersionGCResult,
)
from app.domain.repositories.knowledge_version_repository import (
    KnowledgeVersionGCResult,
)
from app.domain.repositories.uow import IUnitOfWork
from app.domain.runtime_policy import ResourceVersionGcPolicy


class ResourceVersionGCService:
    """Run bounded, reference-safe resource-version collection ticks."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IUnitOfWork],
        policy_reader: OperationsPolicyReader,
        clock: Callable[[], datetime] | None = None,
        object_storage: Any | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._policy_reader = policy_reader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._object_storage = object_storage

    async def collect_knowledge_versions(self) -> KnowledgeVersionGCResult:
        now = self._now(name="knowledge-version GC")
        active = await self._policy_reader.active_operations(require_fresh=True, now=now)
        policy = active.revision.policy.resource_gc.knowledge_base
        if not policy.enabled:
            return KnowledgeVersionGCResult()
        older_than = self._older_than(
            policy,
            now=now,
        )
        async with self._uow_factory() as uow:
            result = await uow.knowledge_version.collect_garbage(
                retain_count=policy.retention_count,
                older_than=older_than,
                batch_size=policy.batch_size,
            )
            await uow.commit()
            return result

    async def collect_codebase_versions(self) -> CodebaseVersionGCResult:
        now = self._now(name="codebase-version GC")
        active = await self._policy_reader.active_operations(require_fresh=True, now=now)
        policy = active.revision.policy.resource_gc.codebase
        if not policy.enabled:
            return CodebaseVersionGCResult()
        older_than = self._older_than(
            policy,
            now=now,
        )
        async with self._uow_factory() as uow:
            result = await uow.codebase_version.collect_garbage(
                retain_count=policy.retention_count,
                older_than=older_than,
                batch_size=policy.batch_size,
            )
            await uow.commit()
        return await self._delete_codebase_snapshots(result)

    def _older_than(
        self,
        policy: ResourceVersionGcPolicy,
        *,
        now: datetime,
    ) -> datetime:
        return now - timedelta(days=policy.retention_min_days)

    def _now(self, *, name: str) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError(f"{name} clock must be timezone-aware")
        return now.astimezone(UTC)

    async def _delete_codebase_snapshots(
        self,
        result: CodebaseVersionGCResult,
    ) -> CodebaseVersionGCResult:
        if not result.snapshot_keys_to_delete or self._object_storage is None:
            return result
        delete_method = (
            getattr(self._object_storage, "delete_bytes", None)
            or getattr(self._object_storage, "delete_object", None)
            or getattr(self._object_storage, "delete", None)
        )
        if delete_method is None:
            return result

        deleted = 0
        for key in result.snapshot_keys_to_delete:
            outcome = delete_method(key)
            if inspect.isawaitable(outcome):
                await outcome
            deleted += 1
        return result.with_deleted_snapshots(deleted)
