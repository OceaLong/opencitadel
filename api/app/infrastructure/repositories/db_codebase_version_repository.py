#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SQLAlchemy repository for immutable codebase analysis versions."""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.codebase import CodebaseStatus
from app.domain.models.codebase_version import (
    CodebaseVersion,
    CodebaseVersionState,
)
from app.domain.models.resource_governance import BuildState, ResourceKind
from app.domain.repositories.codebase_version_repository import (
    CodebaseVersionGCResult,
    CodebaseVersionRepository,
)
from app.infrastructure.models.codebase import (
    CodebaseArtifactModel,
    CodebaseChunkModel,
    CodebaseEdgeModel,
    CodebaseFileModel,
    CodebaseModel,
    CodebaseSymbolModel,
)
from app.infrastructure.models.codebase_version import CodebaseVersionORM
from app.infrastructure.models.resource_governance import (
    ResourceBuildEventORM,
    ResourceBuildORM,
)
from app.infrastructure.repositories.version_gc_base import (
    VersionGarbageCollector,
)


_ACTIVE_BUILD_STATES = (BuildState.QUEUED.value, BuildState.RUNNING.value)
_TERMINAL_BUILD_STATES = (
    BuildState.SUCCEEDED.value,
    BuildState.DEGRADED.value,
    BuildState.FAILED.value,
    BuildState.CANCELLED.value,
)


class DBCodebaseVersionRepository(
    VersionGarbageCollector,
    CodebaseVersionRepository,
):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    @property
    def _version_model(self):
        return CodebaseVersionORM

    @property
    def _resource_model(self):
        return CodebaseModel

    @property
    def _resource_fk_column(self):
        return CodebaseVersionORM.codebase_id

    @property
    def _ranked_cte_name(self) -> str:
        return "ranked_codebase_versions"

    @property
    def _resource_kind(self) -> str:
        return ResourceKind.CODEBASE.value

    @property
    def _active_build_states(self):
        return _ACTIVE_BUILD_STATES

    def _empty_totals(self) -> dict:
        return {
            "deleted_files": 0,
            "deleted_symbols": 0,
            "deleted_edges": 0,
            "deleted_chunks": 0,
            "deleted_artifacts": 0,
            "reclaimed_logical_bytes": 0,
            "deleted_builds": 0,
            "deleted_build_events": 0,
            "retained_shared_snapshots": 0,
        }

    def _empty_extras(self) -> dict:
        return {"snapshot_keys_to_delete": []}

    def _accumulate_deleted(self, totals, extras, deleted) -> None:
        for name, count in deleted.items():
            if name == "snapshot_keys_to_delete":
                extras["snapshot_keys_to_delete"].extend(count)
                continue
            totals[name] += count

    def _build_gc_result(self, *, collected, protected, totals, extras):
        return CodebaseVersionGCResult(
            collected_version_ids=tuple(collected),
            deleted_versions=len(collected),
            protected_active_versions=protected["active"],
            protected_bound_versions=protected["bound"],
            protected_active_build_versions=protected["active_build"],
            protected_age_versions=protected["age"],
            protected_retention_versions=protected["retention"],
            snapshot_keys_to_delete=tuple(
                dict.fromkeys(extras["snapshot_keys_to_delete"])
            ),
            **totals,
        )

    async def _delete_version_closure(
        self,
        version: CodebaseVersionORM,
    ) -> dict[str, int | list[str]]:
        version_id = version.id
        codebase_id = version.codebase_id
        snapshot_key = version.source_snapshot_key
        chunk_result = await self.db_session.execute(
            select(
                CodebaseChunkModel.id,
                CodebaseChunkModel.content,
            ).where(
                CodebaseChunkModel.version_id == version_id,
                CodebaseChunkModel.codebase_id == codebase_id,
            )
        )
        chunk_rows = chunk_result.all()
        reclaimed_logical_bytes = sum(
            len((row.content or "").encode("utf-8"))
            for row in chunk_rows
        )

        deleted_edges = await self._delete_returning_count(
            delete(CodebaseEdgeModel)
            .where(
                CodebaseEdgeModel.version_id == version_id,
                CodebaseEdgeModel.codebase_id == codebase_id,
            )
            .returning(CodebaseEdgeModel.id)
        )
        deleted_chunks = await self._delete_returning_count(
            delete(CodebaseChunkModel)
            .where(
                CodebaseChunkModel.version_id == version_id,
                CodebaseChunkModel.codebase_id == codebase_id,
            )
            .returning(CodebaseChunkModel.id)
        )
        deleted_artifacts = await self._delete_returning_count(
            delete(CodebaseArtifactModel)
            .where(
                CodebaseArtifactModel.version_id == version_id,
                CodebaseArtifactModel.codebase_id == codebase_id,
            )
            .returning(CodebaseArtifactModel.id)
        )
        deleted_symbols = await self._delete_returning_count(
            delete(CodebaseSymbolModel)
            .where(
                CodebaseSymbolModel.version_id == version_id,
                CodebaseSymbolModel.codebase_id == codebase_id,
            )
            .returning(CodebaseSymbolModel.id)
        )
        deleted_files = await self._delete_returning_count(
            delete(CodebaseFileModel)
            .where(
                CodebaseFileModel.version_id == version_id,
                CodebaseFileModel.codebase_id == codebase_id,
            )
            .returning(CodebaseFileModel.id)
        )

        build_result = await self.db_session.execute(
            select(ResourceBuildORM.id).where(
                ResourceBuildORM.resource_kind == ResourceKind.CODEBASE.value,
                ResourceBuildORM.resource_id == codebase_id,
                ResourceBuildORM.version_id == version_id,
                ResourceBuildORM.state.in_(_TERMINAL_BUILD_STATES),
            )
        )
        build_ids = tuple(build_result.scalars().all())
        deleted_build_events = 0
        deleted_builds = 0
        if build_ids:
            deleted_build_events = await self._delete_returning_count(
                delete(ResourceBuildEventORM)
                .where(ResourceBuildEventORM.build_id.in_(build_ids))
                .returning(ResourceBuildEventORM.id)
            )
            deleted_builds = await self._delete_returning_count(
                delete(ResourceBuildORM)
                .where(ResourceBuildORM.id.in_(build_ids))
                .returning(ResourceBuildORM.id)
            )

        retained_shared_snapshots = 0
        snapshot_keys_to_delete: list[str] = []
        if snapshot_key:
            shared_result = await self.db_session.execute(
                select(CodebaseVersionORM.id)
                .where(
                    CodebaseVersionORM.id != version_id,
                    CodebaseVersionORM.source_snapshot_key == snapshot_key,
                )
                .limit(1)
            )
            if shared_result.scalar_one_or_none() is None:
                snapshot_keys_to_delete.append(snapshot_key)
            else:
                retained_shared_snapshots = 1

        await self.db_session.execute(
            update(CodebaseVersionORM)
            .where(
                CodebaseVersionORM.codebase_id == codebase_id,
                CodebaseVersionORM.parent_version_id == version_id,
            )
            .values(parent_version_id=None)
        )
        deleted_versions = await self._delete_returning_count(
            delete(CodebaseVersionORM)
            .where(
                CodebaseVersionORM.id == version_id,
                CodebaseVersionORM.codebase_id == codebase_id,
            )
            .returning(CodebaseVersionORM.id)
        )
        if deleted_versions != 1:
            raise RuntimeError("codebase version GC lost its locked candidate")
        return {
            "deleted_files": deleted_files,
            "deleted_symbols": deleted_symbols,
            "deleted_edges": deleted_edges,
            "deleted_chunks": deleted_chunks,
            "deleted_artifacts": deleted_artifacts,
            "reclaimed_logical_bytes": reclaimed_logical_bytes,
            "deleted_builds": deleted_builds,
            "deleted_build_events": deleted_build_events,
            "retained_shared_snapshots": retained_shared_snapshots,
            "snapshot_keys_to_delete": snapshot_keys_to_delete,
        }

    async def add_version(
        self,
        version: CodebaseVersion,
    ) -> CodebaseVersion:
        record = CodebaseVersionORM.from_domain(version)
        self.db_session.add(record)
        await self.db_session.flush()
        return record.to_domain()

    async def get_version(
        self,
        version_id: str,
        *,
        codebase_id: Optional[str] = None,
    ) -> Optional[CodebaseVersion]:
        stmt = select(CodebaseVersionORM).where(CodebaseVersionORM.id == version_id)
        if codebase_id is not None:
            stmt = stmt.where(CodebaseVersionORM.codebase_id == codebase_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def list_versions(
        self,
        codebase_id: str,
        *,
        limit: int = 500,
        before: Optional[tuple[datetime, str]] = None,
    ) -> list[CodebaseVersion]:
        stmt = select(CodebaseVersionORM).where(
            CodebaseVersionORM.codebase_id == codebase_id
        )
        if before is not None:
            before_created_at, before_id = before
            stmt = stmt.where(
                (CodebaseVersionORM.created_at < before_created_at)
                | (
                    (CodebaseVersionORM.created_at == before_created_at)
                    & (CodebaseVersionORM.id < before_id)
                )
            )
        stmt = stmt.order_by(
            CodebaseVersionORM.created_at.desc(),
            CodebaseVersionORM.id.desc(),
        ).limit(max(1, min(limit, 500)))
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

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
        try:
            state = CodebaseVersionState(state)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "published codebase candidate must be ready or degraded"
            ) from exc
        if state not in {
            CodebaseVersionState.READY,
            CodebaseVersionState.DEGRADED,
        }:
            raise ValueError(
                "published codebase candidate must be ready or degraded"
            )
        if state is CodebaseVersionState.READY and degraded_reasons:
            raise ValueError(
                "ready codebase candidate cannot have degradation reasons"
            )
        if (
            state is CodebaseVersionState.DEGRADED
            and (
                not degraded_reasons
                or any(
                    not isinstance(reason, str) or not reason.strip()
                    for reason in degraded_reasons
                )
            )
        ):
            raise ValueError(
                "degraded codebase candidate requires a stable reason"
            )

        result = await self.db_session.execute(
            select(CodebaseVersionORM)
            .where(CodebaseVersionORM.id == version_id)
            .with_for_update()
        )
        version = result.scalar_one_or_none()
        if version is None:
            return False
        cb_result = await self.db_session.execute(
            select(CodebaseModel)
            .where(CodebaseModel.id == version.codebase_id)
            .with_for_update()
        )
        codebase = cb_result.scalar_one_or_none()
        if codebase is None:
            return False
        if codebase.active_version_id != expected_active_version_id:
            return False
        now = datetime.now(timezone.utc)
        version.state = state.value
        version.capabilities = dict(capabilities)
        version.degraded_reasons = list(degraded_reasons)
        version.metrics = dict(metrics)
        version.published_at = now
        codebase.active_version_id = version.id
        codebase.status = CodebaseStatus.READY.value
        codebase.vector_degraded = state is CodebaseVersionState.DEGRADED
        codebase.updated_at = now
        await self.db_session.flush()
        return True

    async def update_snapshot(
        self,
        version_id: str,
        *,
        source_snapshot_key: str,
        source_revision: str,
        source_digest: str,
    ) -> CodebaseVersion:
        result = await self.db_session.execute(
            select(CodebaseVersionORM)
            .where(CodebaseVersionORM.id == version_id)
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise ValueError("codebase version not found")
        record.source_snapshot_key = source_snapshot_key
        record.source_revision = source_revision
        record.source_digest = source_digest
        await self.db_session.flush()
        return record.to_domain()

    async def mark_failed(
        self,
        version_id: str,
        *,
        error: str,
    ) -> CodebaseVersion:
        result = await self.db_session.execute(
            select(CodebaseVersionORM)
            .where(CodebaseVersionORM.id == version_id)
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise ValueError("codebase version not found")
        record.state = CodebaseVersionState.FAILED.value
        record.metrics = {**dict(record.metrics or {}), "error": error}
        await self.db_session.flush()
        return record.to_domain()
