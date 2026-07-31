#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared build-lifecycle projection/consistency-assertion helpers.

``CodebaseService`` and ``KnowledgeBaseService`` both project a
``ResourceBuild`` and its candidate/owner/publication invariants into a
read model. The *consistency assertions* (owner closure, candidate closure,
version publication, active-version-published, build-closure) are performed
in an identical order in both services; only resource-specific naming
diverges: the ``ResourceKind`` discriminant, the version-repository accessor
(``codebase_id`` vs ``knowledge_base_id``), the concrete projection DTO, and
the exact error strings. Those error strings are part of the API contract and
must stay byte-identical, so each subclass supplies them verbatim as class
attributes; the base only chooses *when* to raise them.

Concrete classes mixing this in must provide:

- ``_build_resource_kind``: the ``ResourceKind`` this service owns.
- ``_published_version_states``: the set of version states that count as a
  *published* version for this resource type (a resource-specific enum, so it
  cannot be shared as a single constant).
- The six ``_*_error`` class attributes below, each equal to the original
  literal so the raised message is unchanged.
- ``_get_projection_version(uow, version_id, resource_id)``: fetch the version
  row scoped to the resource (wraps the resource-specific repository kwarg).
- ``_build_projection(build, *, active_version_id)``: build the resource's
  build-projection DTO (kept in the subclass because the DTO class, its
  ``*_id`` field name and per-resource ``metrics`` handling differ).
- ``_build_version_projection(version, *, is_active, is_published,
  is_candidate, build)``: build the resource's version-projection DTO (kept in
  the subclass for the same reasons, plus codebase-only ``source_*`` fields).

Not hoisted (documented in each subclass): ``cancel_build`` and
``_project_candidate_result`` carry genuine semantic differences, not just
naming, so they remain overridden per resource.
"""
from typing import Any, Optional

from app.application.errors.exceptions import ConflictError
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceKind,
)
from app.domain.repositories.uow import IUnitOfWork

# Build lifecycle state sets. Identical values in both resources, so shared
# here (this replaces the previously inlined ``{FAILED, CANCELLED}`` /
# ``{QUEUED, RUNNING}`` literals on the knowledge side, aligning it with the
# codebase side's named-constant style; values are unchanged).
_ACTIVE_BUILD_STATES = {BuildState.QUEUED, BuildState.RUNNING}
_RETRYABLE_BUILD_STATES = {BuildState.FAILED, BuildState.CANCELLED}


class ResourceBuildSupport:
    """Mixin: shared build/version projection consistency assertions."""

    # Shared, resource-agnostic state sets (see module-level definitions).
    _ACTIVE_BUILD_STATES = _ACTIVE_BUILD_STATES
    _RETRYABLE_BUILD_STATES = _RETRYABLE_BUILD_STATES

    # --- subclass-provided hooks -------------------------------------------
    _build_resource_kind: ResourceKind
    _published_version_states: Any

    _build_owner_closure_error: str
    _build_candidate_closure_error: str
    _version_publication_error: str
    _active_version_not_published_error: str
    _version_build_missing_error: str
    _version_build_closure_error: str

    async def _get_projection_version(
        self,
        uow: IUnitOfWork,
        version_id: str,
        resource_id: str,
    ) -> Any:
        raise NotImplementedError

    def _build_projection(
        self,
        build: ResourceBuild,
        *,
        active_version_id: Optional[str],
    ) -> Any:
        raise NotImplementedError

    def _build_version_projection(
        self,
        version: Any,
        *,
        is_active: bool,
        is_published: bool,
        is_candidate: bool,
        build: Any,
    ) -> Any:
        raise NotImplementedError

    # --- shared consistency assertions -------------------------------------
    async def _project_build(
        self,
        uow: IUnitOfWork,
        resource_id: str,
        build: ResourceBuild,
        *,
        active_version_id: Optional[str],
    ) -> Any:
        if (
            build.resource_kind is not self._build_resource_kind
            or build.resource_id != resource_id
        ):
            raise ConflictError(self._build_owner_closure_error)
        candidate = await self._get_projection_version(
            uow,
            build.version_id,
            resource_id,
        )
        if (
            candidate is None
            or candidate.build_id != build.id
            or candidate.parent_version_id != build.parent_version_id
            or (
                build.state in self._ACTIVE_BUILD_STATES
                and build.parent_version_id != active_version_id
            )
        ):
            raise ConflictError(self._build_candidate_closure_error)
        return self._build_projection(
            build,
            active_version_id=active_version_id,
        )

    async def _project_version(
        self,
        uow: IUnitOfWork,
        resource: Any,
        version: Any,
    ) -> Any:
        is_published = (
            version.published_at is not None
            and version.state in self._published_version_states
        )
        if (
            version.published_at is None
            and version.state in self._published_version_states
        ) or (
            version.published_at is not None
            and version.state not in self._published_version_states
        ):
            raise ConflictError(self._version_publication_error)
        if version.id == resource.active_version_id and not is_published:
            raise ConflictError(self._active_version_not_published_error)
        build_projection = None
        if version.build_id is not None:
            build = await uow.resource_governance.get_build(version.build_id)
            if build is None:
                raise ConflictError(self._version_build_missing_error)
            build_projection = await self._project_build(
                uow,
                resource.id,
                build,
                active_version_id=resource.active_version_id,
            )
            if (
                build.version_id != version.id
                or build.parent_version_id != version.parent_version_id
            ):
                raise ConflictError(self._version_build_closure_error)
        return self._build_version_projection(
            version,
            is_active=version.id == resource.active_version_id,
            is_published=is_published,
            is_candidate=not is_published,
            build=build_projection,
        )
