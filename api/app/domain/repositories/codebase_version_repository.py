#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Repository contract for immutable codebase analysis versions."""
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional, Protocol

from app.domain.models.codebase_version import (
    CodebaseVersion,
    CodebaseVersionState,
)


@dataclass(frozen=True)
class CodebaseVersionGCResult:
    """Secret-free, deterministic counters from one codebase GC transaction."""

    collected_version_ids: tuple[str, ...] = ()
    deleted_versions: int = 0
    deleted_files: int = 0
    deleted_symbols: int = 0
    deleted_edges: int = 0
    deleted_chunks: int = 0
    deleted_artifacts: int = 0
    reclaimed_logical_bytes: int = 0
    deleted_builds: int = 0
    deleted_build_events: int = 0
    deleted_snapshots: int = 0
    retained_shared_snapshots: int = 0
    protected_active_versions: int = 0
    protected_bound_versions: int = 0
    protected_active_build_versions: int = 0
    protected_age_versions: int = 0
    protected_retention_versions: int = 0
    snapshot_keys_to_delete: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "collected_version_ids",
            tuple(self.collected_version_ids),
        )
        object.__setattr__(
            self,
            "snapshot_keys_to_delete",
            tuple(
                key
                for key in self.snapshot_keys_to_delete
                if isinstance(key, str) and key
            ),
        )
        counters = (
            self.deleted_versions,
            self.deleted_files,
            self.deleted_symbols,
            self.deleted_edges,
            self.deleted_chunks,
            self.deleted_artifacts,
            self.reclaimed_logical_bytes,
            self.deleted_builds,
            self.deleted_build_events,
            self.deleted_snapshots,
            self.retained_shared_snapshots,
            self.protected_active_versions,
            self.protected_bound_versions,
            self.protected_active_build_versions,
            self.protected_age_versions,
            self.protected_retention_versions,
        )
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for value in counters
        ):
            raise ValueError("codebase-version GC counters must be non-negative")
        if self.deleted_versions != len(self.collected_version_ids):
            raise ValueError(
                "deleted version count must match collected version identities"
            )

    @property
    def retained_reference_count(self) -> int:
        return (
            self.protected_bound_versions
            + self.protected_active_build_versions
            + self.retained_shared_snapshots
        )

    def with_deleted_snapshots(self, count: int) -> "CodebaseVersionGCResult":
        return replace(self, deleted_snapshots=count)

    def metrics(self) -> dict[str, int]:
        """Return stable, non-content metrics suitable for logs and audits."""
        return {
            "collected_versions": len(self.collected_version_ids),
            "deleted_artifacts": self.deleted_artifacts,
            "deleted_build_events": self.deleted_build_events,
            "deleted_builds": self.deleted_builds,
            "deleted_chunks": self.deleted_chunks,
            "deleted_edges": self.deleted_edges,
            "deleted_files": self.deleted_files,
            "deleted_snapshots": self.deleted_snapshots,
            "deleted_symbols": self.deleted_symbols,
            "deleted_versions": self.deleted_versions,
            "protected_active_build_versions": (
                self.protected_active_build_versions
            ),
            "protected_active_versions": self.protected_active_versions,
            "protected_age_versions": self.protected_age_versions,
            "protected_bound_versions": self.protected_bound_versions,
            "protected_retention_versions": (
                self.protected_retention_versions
            ),
            "reclaimed_logical_bytes": self.reclaimed_logical_bytes,
            "retained_reference_count": self.retained_reference_count,
            "retained_shared_snapshots": self.retained_shared_snapshots,
        }


class CodebaseVersionRepository(Protocol):
    async def collect_garbage(
        self,
        *,
        retain_count: int,
        older_than: datetime,
        batch_size: int,
    ) -> CodebaseVersionGCResult:
        """Collect one bounded, reference-safe batch in the current UoW."""
        ...

    async def add_version(
        self,
        version: CodebaseVersion,
    ) -> CodebaseVersion:
        ...

    async def get_version(
        self,
        version_id: str,
        *,
        codebase_id: Optional[str] = None,
    ) -> Optional[CodebaseVersion]:
        ...

    async def list_versions(
        self,
        codebase_id: str,
        *,
        limit: int = 500,
        before: Optional[tuple[datetime, str]] = None,
    ) -> list[CodebaseVersion]:
        ...

    async def publish_candidate(
        self,
        version_id: str,
        *,
        expected_active_version_id: Optional[str],
        state: CodebaseVersionState,
        capabilities: dict[str, bool],
        degraded_reasons: list[str],
        metrics: dict,
    ) -> bool:
        ...

    async def update_snapshot(
        self,
        version_id: str,
        *,
        source_snapshot_key: str,
        source_revision: str,
        source_digest: str,
    ) -> CodebaseVersion:
        ...

    async def mark_failed(
        self,
        version_id: str,
        *,
        error: str,
    ) -> CodebaseVersion:
        ...
