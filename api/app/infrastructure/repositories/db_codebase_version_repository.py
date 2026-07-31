#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SQLAlchemy repository for immutable codebase analysis versions."""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, delete, exists, func, or_, select, update
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
    SessionResourceBindingORM,
)


_ACTIVE_BUILD_STATES = (BuildState.QUEUED.value, BuildState.RUNNING.value)
_TERMINAL_BUILD_STATES = (
    BuildState.SUCCEEDED.value,
    BuildState.DEGRADED.value,
    BuildState.FAILED.value,
    BuildState.CANCELLED.value,
)


class DBCodebaseVersionRepository(CodebaseVersionRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def collect_garbage(
        self,
        *,
        retain_count: int,
        older_than: datetime,
        batch_size: int,
    ) -> CodebaseVersionGCResult:
        """Collect one deterministic batch under codebase -> version locks."""
        if (
            not isinstance(retain_count, int)
            or isinstance(retain_count, bool)
            or retain_count < 0
        ):
            raise ValueError("retain_count must be a non-negative integer")
        if not isinstance(older_than, datetime) or older_than.tzinfo is None:
            raise ValueError("GC cutoff must be timezone-aware")
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or not 1 <= batch_size <= 500
        ):
            raise ValueError("batch_size must be between 1 and 500")

        ranked = self._ranked_versions()
        bound = self._binding_reference_exists(
            ranked.c.codebase_id,
            ranked.c.version_id,
        )
        active_build = self._active_build_exists(
            ranked.c.codebase_id,
            ranked.c.version_id,
        )
        candidates_result = await self.db_session.execute(
            select(
                ranked.c.version_id,
                ranked.c.codebase_id,
                ranked.c.created_at,
            )
            .join(
                CodebaseModel,
                CodebaseModel.id == ranked.c.codebase_id,
            )
            .where(
                ranked.c.retention_rank > retain_count,
                ranked.c.created_at < older_than,
                or_(
                    CodebaseModel.active_version_id.is_(None),
                    CodebaseModel.active_version_id != ranked.c.version_id,
                ),
                ~bound,
                ~active_build,
            )
            .order_by(
                ranked.c.created_at.asc(),
                ranked.c.codebase_id.asc(),
                ranked.c.version_id.asc(),
            )
            .limit(batch_size)
        )
        candidate_rows = candidates_result.all()

        protected = await self._protected_counts(
            ranked=ranked,
            older_than=older_than,
            retain_count=retain_count,
        )
        totals = {
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
        snapshot_keys_to_delete: list[str] = []
        collected: list[str] = []
        for candidate in candidate_rows:
            codebase_id = candidate.codebase_id
            version_id = candidate.version_id

            # Shared mutex and lock order used by publish and binding writes.
            codebase_result = await self.db_session.execute(
                select(CodebaseModel)
                .where(CodebaseModel.id == codebase_id)
                .with_for_update()
            )
            codebase = codebase_result.scalar_one_or_none()
            if codebase is None or codebase.active_version_id == version_id:
                continue
            version_result = await self.db_session.execute(
                select(CodebaseVersionORM)
                .where(
                    CodebaseVersionORM.id == version_id,
                    CodebaseVersionORM.codebase_id == codebase_id,
                )
                .with_for_update()
            )
            version = version_result.scalar_one_or_none()
            if version is None:
                continue
            if not await self._candidate_still_collectible(
                version,
                retain_count=retain_count,
                older_than=older_than,
            ):
                continue
            deleted = await self._delete_version_closure(version)
            for name, count in deleted.items():
                if name == "snapshot_keys_to_delete":
                    snapshot_keys_to_delete.extend(count)
                    continue
                totals[name] += count
            collected.append(version_id)

        await self.db_session.flush()
        return CodebaseVersionGCResult(
            collected_version_ids=tuple(collected),
            deleted_versions=len(collected),
            protected_active_versions=protected["active"],
            protected_bound_versions=protected["bound"],
            protected_active_build_versions=protected["active_build"],
            protected_age_versions=protected["age"],
            protected_retention_versions=protected["retention"],
            snapshot_keys_to_delete=tuple(dict.fromkeys(snapshot_keys_to_delete)),
            **totals,
        )

    @staticmethod
    def _ranked_versions():
        return (
            select(
                CodebaseVersionORM.id.label("version_id"),
                CodebaseVersionORM.codebase_id.label("codebase_id"),
                CodebaseVersionORM.created_at.label("created_at"),
                func.row_number()
                .over(
                    partition_by=CodebaseVersionORM.codebase_id,
                    order_by=(
                        CodebaseVersionORM.created_at.desc(),
                        CodebaseVersionORM.id.desc(),
                    ),
                )
                .label("retention_rank"),
            )
            .cte("ranked_codebase_versions")
        )

    @staticmethod
    def _binding_reference_exists(codebase_id, version_id):
        return exists(
            select(SessionResourceBindingORM.id).where(
                SessionResourceBindingORM.resource_kind
                == ResourceKind.CODEBASE.value,
                SessionResourceBindingORM.resource_id == codebase_id,
                SessionResourceBindingORM.version_id == version_id,
            )
        )

    @staticmethod
    def _active_build_exists(codebase_id, version_id):
        return exists(
            select(ResourceBuildORM.id).where(
                ResourceBuildORM.resource_kind == ResourceKind.CODEBASE.value,
                ResourceBuildORM.resource_id == codebase_id,
                ResourceBuildORM.version_id == version_id,
                ResourceBuildORM.state.in_(_ACTIVE_BUILD_STATES),
            )
        )

    async def _protected_counts(
        self,
        *,
        ranked,
        older_than: datetime,
        retain_count: int,
    ) -> dict[str, int]:
        active = await self.db_session.execute(
            select(func.count())
            .select_from(CodebaseVersionORM)
            .join(
                CodebaseModel,
                CodebaseModel.id == CodebaseVersionORM.codebase_id,
            )
            .where(CodebaseModel.active_version_id == CodebaseVersionORM.id)
        )
        bound = await self.db_session.execute(
            select(func.count())
            .select_from(CodebaseVersionORM)
            .where(
                self._binding_reference_exists(
                    CodebaseVersionORM.codebase_id,
                    CodebaseVersionORM.id,
                )
            )
        )
        active_build = await self.db_session.execute(
            select(func.count())
            .select_from(CodebaseVersionORM)
            .where(
                self._active_build_exists(
                    CodebaseVersionORM.codebase_id,
                    CodebaseVersionORM.id,
                )
            )
        )
        age = await self.db_session.execute(
            select(func.count())
            .select_from(CodebaseVersionORM)
            .where(CodebaseVersionORM.created_at >= older_than)
        )
        retention = await self.db_session.execute(
            select(func.count())
            .select_from(ranked)
            .where(ranked.c.retention_rank <= retain_count)
        )
        return {
            "active": int(active.scalar_one()),
            "bound": int(bound.scalar_one()),
            "active_build": int(active_build.scalar_one()),
            "age": int(age.scalar_one()),
            "retention": int(retention.scalar_one()),
        }

    async def _candidate_still_collectible(
        self,
        version: CodebaseVersionORM,
        *,
        retain_count: int,
        older_than: datetime,
    ) -> bool:
        created_at = version.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if created_at >= older_than:
            return False
        newer_result = await self.db_session.execute(
            select(func.count())
            .select_from(CodebaseVersionORM)
            .where(
                CodebaseVersionORM.codebase_id == version.codebase_id,
                or_(
                    CodebaseVersionORM.created_at > version.created_at,
                    and_(
                        CodebaseVersionORM.created_at == version.created_at,
                        CodebaseVersionORM.id > version.id,
                    ),
                ),
            )
        )
        if int(newer_result.scalar_one()) < retain_count:
            return False
        bound_result = await self.db_session.execute(
            select(
                self._binding_reference_exists(
                    version.codebase_id,
                    version.id,
                )
            )
        )
        if bool(bound_result.scalar_one()):
            return False
        active_build_result = await self.db_session.execute(
            select(
                self._active_build_exists(
                    version.codebase_id,
                    version.id,
                )
            )
        )
        return not bool(active_build_result.scalar_one())

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

    async def _delete_returning_count(self, statement) -> int:
        result = await self.db_session.execute(statement)
        return len(result.scalars().all())

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
