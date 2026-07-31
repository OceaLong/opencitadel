#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Application boundary for bounded immutable resource-version retention."""
import inspect
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domain.repositories.codebase_version_repository import (
    CodebaseVersionGCResult,
)
from app.domain.repositories.knowledge_version_repository import (
    KnowledgeVersionGCResult,
)
from app.domain.repositories.uow import IUnitOfWork


class ResourceVersionGCService:
    """Run bounded, reference-safe resource-version collection ticks."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IUnitOfWork],
        clock: Callable[[], datetime] | None = None,
        object_storage: Any | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._object_storage = object_storage

    async def collect_knowledge_versions(
        self,
        retain_count: int,
        min_age_days: int,
        batch_size: int,
    ) -> KnowledgeVersionGCResult:
        older_than = self._older_than(
            retain_count,
            min_age_days,
            batch_size,
            name="knowledge-version GC",
        )
        async with self._uow_factory() as uow:
            return await uow.knowledge_version.collect_garbage(
                retain_count=retain_count,
                older_than=older_than,
                batch_size=batch_size,
            )

    async def collect_codebase_versions(
        self,
        retain_count: int,
        min_age_days: int,
        batch_size: int,
    ) -> CodebaseVersionGCResult:
        older_than = self._older_than(
            retain_count,
            min_age_days,
            batch_size,
            name="codebase-version GC",
        )
        async with self._uow_factory() as uow:
            result = await uow.codebase_version.collect_garbage(
                retain_count=retain_count,
                older_than=older_than,
                batch_size=batch_size,
            )
        return await self._delete_codebase_snapshots(result)

    def _older_than(
        self,
        retain_count: int,
        min_age_days: int,
        batch_size: int,
        *,
        name: str,
    ) -> datetime:
        if (
            not isinstance(retain_count, int)
            or isinstance(retain_count, bool)
            or retain_count < 0
        ):
            raise ValueError("retain_count must be a non-negative integer")
        if (
            not isinstance(min_age_days, int)
            or isinstance(min_age_days, bool)
            or min_age_days < 0
        ):
            raise ValueError("min_age_days must be a non-negative integer")
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or not 1 <= batch_size <= 500
        ):
            raise ValueError("batch_size must be between 1 and 500")
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError(f"{name} clock must be timezone-aware")
        return now.astimezone(timezone.utc) - timedelta(days=min_age_days)

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
