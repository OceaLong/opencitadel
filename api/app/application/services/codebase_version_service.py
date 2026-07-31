#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Owner-scoped ResourceVersionProvider for codebase analysis versions."""
from collections.abc import Callable
from datetime import datetime

from app.application.services.resource_version_provider import (
    OwnerScopedVersionProvider,
)
from app.domain.models.codebase import Codebase
from app.domain.models.codebase_version import (
    CodebaseVersion,
    CodebaseVersionState,
)
from app.domain.models.resource_governance import ResourceKind
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.codebase.version_builder import (
    CodebaseBuildPlan,
    CodebaseVersionBuilder,
)


class CodebaseVersionService(OwnerScopedVersionProvider[Codebase, CodebaseVersion]):
    kind = ResourceKind.CODEBASE
    _resource_label = "codebase"
    _version_label = "codebase version"
    _cursor_label = "codebase-version"

    def __init__(self, *, uow_factory: Callable[[], IUnitOfWork]) -> None:
        super().__init__(uow_factory=uow_factory)
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

    async def _get_resource(
        self, uow: IUnitOfWork, resource_id: str, scope: OwnerScope
    ) -> Codebase | None:
        return await uow.codebase.get_by_id(resource_id, scope=scope)

    async def _get_version(
        self, uow: IUnitOfWork, version_id: str, resource_id: str
    ) -> CodebaseVersion | None:
        return await uow.codebase_version.get_version(
            version_id,
            codebase_id=resource_id,
        )

    async def _list_page(
        self,
        uow: IUnitOfWork,
        resource_id: str,
        *,
        limit: int,
        before: tuple[datetime, str] | None,
    ) -> list[CodebaseVersion]:
        return await uow.codebase_version.list_versions(
            resource_id,
            limit=limit,
            before=before,
        )

    @classmethod
    def _is_published(cls, version: CodebaseVersion) -> bool:
        return (
            version.published_at is not None
            and version.state
            in {
                CodebaseVersionState.READY,
                CodebaseVersionState.DEGRADED,
            }
        )

    @classmethod
    def _is_degraded(cls, version: CodebaseVersion) -> bool:
        return version.state is CodebaseVersionState.DEGRADED

    @classmethod
    def _resource_id_of(cls, version: CodebaseVersion) -> str:
        return version.codebase_id
