#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared GC skeleton for immutable resource-version repositories.

`VersionGarbageCollector` lifts the reference-safe, deterministic garbage
collection skeleton shared by the knowledge-base and codebase version
repositories.  The lock order (resource row -> version row, both
``with_for_update``), the batch selection query, the ranked-version CTE, the
protected-count aggregation, the timezone normalisation in the collectibility
re-check, and the delete-and-count helper are byte-for-byte the same between the
two repositories and are therefore lifted here verbatim.

Only the pieces that genuinely differ by domain stay in the subclasses, exposed
here as abstract hooks:

* ORM identity (``_version_model``, ``_resource_model``, ``_resource_fk_column``,
  ``_ranked_cte_name``) and governance identity (``_resource_kind``,
  ``_active_build_states``) -- pure naming / ORM differences.
* ``_delete_version_closure`` -- the per-domain dependency closure delete
  (KB: manifests/revisions/chunks/entities/relations/entity_refs; CB:
  files/symbols/edges/chunks/artifacts + snapshot bookkeeping).
* ``_empty_totals`` / ``_empty_extras`` / ``_accumulate_deleted`` /
  ``_build_gc_result`` -- the result assembly.  These stay in the subclasses
  because the two GC results carry different counter fields, and, more
  importantly, because the two ``collect_garbage`` merge loops are *not*
  identical: the codebase repository routes the non-integer
  ``snapshot_keys_to_delete`` list out of the integer totals before summing,
  while the knowledge-base repository sums every returned counter directly.
  That is a real semantic difference, so the merge loop is kept in the
  subclasses rather than lifted.
"""
import abc
from datetime import datetime, timezone

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.models.resource_governance import (
    ResourceBuildORM,
    SessionResourceBindingORM,
)


class VersionGarbageCollector(abc.ABC):
    """Reference-safe, deterministic, one-batch version GC skeleton."""

    db_session: AsyncSession

    # ------------------------------------------------------------------
    # Domain hooks
    # ------------------------------------------------------------------
    @property
    @abc.abstractmethod
    def _version_model(self):
        """The version ORM class (e.g. ``KnowledgeBaseVersionORM``)."""

    @property
    @abc.abstractmethod
    def _resource_model(self):
        """The owning resource ORM class (e.g. ``KnowledgeBaseModel``)."""

    @property
    @abc.abstractmethod
    def _resource_fk_column(self):
        """The version -> resource foreign-key column on the version ORM."""

    @property
    @abc.abstractmethod
    def _ranked_cte_name(self) -> str:
        """The CTE alias used by :meth:`_ranked_versions`."""

    @property
    @abc.abstractmethod
    def _resource_kind(self) -> str:
        """The ``ResourceKind`` value for governance lookups."""

    @property
    @abc.abstractmethod
    def _active_build_states(self):
        """The build states that block collection (queued/running)."""

    @abc.abstractmethod
    async def _delete_version_closure(self, version) -> dict:
        """Delete the per-domain dependency closure and return counters."""

    @abc.abstractmethod
    def _empty_totals(self) -> dict:
        """Return a zeroed per-domain totals accumulator."""

    @abc.abstractmethod
    def _empty_extras(self) -> dict:
        """Return a fresh per-domain non-counter accumulator."""

    @abc.abstractmethod
    def _accumulate_deleted(self, totals: dict, extras: dict, deleted: dict) -> None:
        """Merge one ``_delete_version_closure`` result into the accumulators."""

    @abc.abstractmethod
    def _build_gc_result(self, *, collected, protected, totals, extras):
        """Assemble the per-domain GC result dataclass."""

    # ------------------------------------------------------------------
    # Lifted skeleton
    # ------------------------------------------------------------------
    async def collect_garbage(
        self,
        *,
        retain_count: int,
        older_than: datetime,
        batch_size: int,
    ):
        """Collect one deterministic batch under resource -> version row locks."""
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

        resource_model = self._resource_model
        version_model = self._version_model
        fk = self._resource_fk_column
        fk_key = fk.key

        ranked = self._ranked_versions()
        ranked_resource_id = ranked.c[fk_key]
        bound = self._binding_reference_exists(
            ranked_resource_id,
            ranked.c.version_id,
        )
        active_build = self._active_build_exists(
            ranked_resource_id,
            ranked.c.version_id,
        )
        candidates_result = await self.db_session.execute(
            select(
                ranked.c.version_id,
                ranked_resource_id,
                ranked.c.created_at,
            )
            .join(
                resource_model,
                resource_model.id == ranked_resource_id,
            )
            .where(
                ranked.c.retention_rank > retain_count,
                ranked.c.created_at < older_than,
                or_(
                    resource_model.active_version_id.is_(None),
                    resource_model.active_version_id != ranked.c.version_id,
                ),
                ~bound,
                ~active_build,
            )
            .order_by(
                ranked.c.created_at.asc(),
                ranked_resource_id.asc(),
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
        totals = self._empty_totals()
        extras = self._empty_extras()
        collected: list[str] = []
        for candidate in candidate_rows:
            resource_id = getattr(candidate, fk_key)
            version_id = candidate.version_id

            # Shared mutex and lock order used by publish and binding writes.
            resource_result = await self.db_session.execute(
                select(resource_model)
                .where(resource_model.id == resource_id)
                .with_for_update()
            )
            resource = resource_result.scalar_one_or_none()
            if resource is None or resource.active_version_id == version_id:
                continue
            version_result = await self.db_session.execute(
                select(version_model)
                .where(
                    version_model.id == version_id,
                    fk == resource_id,
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
            self._accumulate_deleted(totals, extras, deleted)
            collected.append(version_id)

        await self.db_session.flush()
        return self._build_gc_result(
            collected=collected,
            protected=protected,
            totals=totals,
            extras=extras,
        )

    def _ranked_versions(self):
        version_model = self._version_model
        fk = self._resource_fk_column
        return (
            select(
                version_model.id.label("version_id"),
                fk.label(fk.key),
                version_model.created_at.label("created_at"),
                func.row_number()
                .over(
                    partition_by=fk,
                    order_by=(
                        version_model.created_at.desc(),
                        version_model.id.desc(),
                    ),
                )
                .label("retention_rank"),
            )
            .cte(self._ranked_cte_name)
        )

    def _binding_reference_exists(self, resource_id, version_id):
        return exists(
            select(SessionResourceBindingORM.id).where(
                SessionResourceBindingORM.resource_kind == self._resource_kind,
                SessionResourceBindingORM.resource_id == resource_id,
                SessionResourceBindingORM.version_id == version_id,
            )
        )

    def _active_build_exists(self, resource_id, version_id):
        return exists(
            select(ResourceBuildORM.id).where(
                ResourceBuildORM.resource_kind == self._resource_kind,
                ResourceBuildORM.resource_id == resource_id,
                ResourceBuildORM.version_id == version_id,
                ResourceBuildORM.state.in_(self._active_build_states),
            )
        )

    async def _protected_counts(
        self,
        *,
        ranked,
        older_than: datetime,
        retain_count: int,
    ) -> dict[str, int]:
        version_model = self._version_model
        resource_model = self._resource_model
        fk = self._resource_fk_column
        active = await self.db_session.execute(
            select(func.count())
            .select_from(version_model)
            .join(
                resource_model,
                resource_model.id == fk,
            )
            .where(resource_model.active_version_id == version_model.id)
        )
        bound = await self.db_session.execute(
            select(func.count())
            .select_from(version_model)
            .where(
                self._binding_reference_exists(
                    fk,
                    version_model.id,
                )
            )
        )
        active_build = await self.db_session.execute(
            select(func.count())
            .select_from(version_model)
            .where(
                self._active_build_exists(
                    fk,
                    version_model.id,
                )
            )
        )
        age = await self.db_session.execute(
            select(func.count())
            .select_from(version_model)
            .where(version_model.created_at >= older_than)
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
        version,
        *,
        retain_count: int,
        older_than: datetime,
    ) -> bool:
        version_model = self._version_model
        fk = self._resource_fk_column
        resource_id = getattr(version, fk.key)
        created_at = version.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if created_at >= older_than:
            return False
        newer_result = await self.db_session.execute(
            select(func.count())
            .select_from(version_model)
            .where(
                fk == resource_id,
                or_(
                    version_model.created_at > version.created_at,
                    and_(
                        version_model.created_at == version.created_at,
                        version_model.id > version.id,
                    ),
                ),
            )
        )
        if int(newer_result.scalar_one()) < retain_count:
            return False
        bound_result = await self.db_session.execute(
            select(
                self._binding_reference_exists(
                    resource_id,
                    version.id,
                )
            )
        )
        if bool(bound_result.scalar_one()):
            return False
        active_build_result = await self.db_session.execute(
            select(
                self._active_build_exists(
                    resource_id,
                    version.id,
                )
            )
        )
        return not bool(active_build_result.scalar_one())

    async def _delete_returning_count(self, statement) -> int:
        result = await self.db_session.execute(statement)
        return len(result.scalars().all())
