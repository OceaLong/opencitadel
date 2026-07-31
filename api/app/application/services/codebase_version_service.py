#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Owner-scoped ResourceVersionProvider for codebase analysis versions."""
from collections.abc import Callable
from datetime import datetime

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.domain.models.codebase_version import (
    CodebaseVersion,
    CodebaseVersionState,
)
from app.domain.models.resource_governance import (
    BuildState,
    PublishedResourceVersion,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.codebase.version_builder import (
    CodebaseBuildPlan,
    CodebaseVersionBuilder,
)


class CodebaseVersionService:
    kind = ResourceKind.CODEBASE
    _PAGE_SIZE = 500

    def __init__(self, *, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory
        self._builder = CodebaseVersionBuilder(uow_factory)

    async def create_reanalysis(
        self,
        codebase_id: str,
        *,
        actor_id: str,
        scope: OwnerScope,
    ) -> CodebaseBuildPlan:
        return await self._builder.create_reanalysis(
            codebase_id,
            actor_id=actor_id,
            scope=scope,
        )

    async def resolve_published_version(
        self,
        resource_id: str,
        requested_version_id: str | None,
        scope: OwnerScope,
    ) -> PublishedResourceVersion:
        async with self._uow_factory() as uow:
            codebase = await uow.codebase.get_by_id(resource_id, scope=scope)
            if codebase is None:
                raise NotFoundError("codebase not found in owner scope")
            version_id = requested_version_id or codebase.active_version_id
            if version_id is None:
                raise BadRequestError("codebase has no published version")
            version = await uow.codebase_version.get_version(
                version_id,
                codebase_id=resource_id,
            )
            if version is None:
                if requested_version_id is None:
                    raise BadRequestError(
                        "active codebase version is not published"
                    )
                raise NotFoundError(
                    "codebase version not found in owner scope"
                )
            return self._published_projection(version)

    async def list_published_versions(
        self,
        resource_id: str,
        scope: OwnerScope,
    ) -> list[PublishedResourceVersion]:
        async with self._uow_factory() as uow:
            codebase = await uow.codebase.get_by_id(resource_id, scope=scope)
            if codebase is None:
                raise NotFoundError("codebase not found in owner scope")
            versions: list[CodebaseVersion] = []
            before: tuple[datetime, str] | None = None
            while True:
                page = await uow.codebase_version.list_versions(
                    resource_id,
                    limit=self._PAGE_SIZE,
                    before=before,
                )
                versions.extend(page)
                if len(page) < self._PAGE_SIZE:
                    break
                before = (page[-1].created_at, page[-1].id)
            return [
                self._published_projection(version)
                for version in versions
                if self._is_published(version)
            ]

    @staticmethod
    def _is_published(version: CodebaseVersion) -> bool:
        return (
            version.published_at is not None
            and version.state
            in {
                CodebaseVersionState.READY,
                CodebaseVersionState.DEGRADED,
            }
        )

    @classmethod
    def _published_projection(
        cls,
        version: CodebaseVersion,
    ) -> PublishedResourceVersion:
        if not cls._is_published(version):
            raise BadRequestError("codebase version is not published")
        degraded = version.state is CodebaseVersionState.DEGRADED
        if degraded and not version.degraded_reasons:
            raise BadRequestError(
                "degraded codebase version lacks degradation reason"
            )
        return PublishedResourceVersion(
            resource_kind=ResourceKind.CODEBASE,
            resource_id=version.codebase_id,
            version_id=version.id,
            state=BuildState.DEGRADED if degraded else BuildState.SUCCEEDED,
            published=True,
            degraded=degraded,
            capabilities=dict(version.capabilities),
            degraded_reasons=list(version.degraded_reasons),
        )
