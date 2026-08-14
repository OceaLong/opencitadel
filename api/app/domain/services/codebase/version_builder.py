#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Create codebase analysis candidate versions and durable builds."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from app.domain.errors import NotFoundError
from app.domain.models.codebase_version import (
    CodebaseVersion,
    CodebaseVersionState,
)
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork


@dataclass(frozen=True)
class CodebaseBuildPlan:
    version: CodebaseVersion
    build: ResourceBuild
    existing: bool = False


class CodebaseVersionBuilder:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def create_reanalysis(
        self,
        codebase_id: str,
        *,
        actor_id: str,
        scope: OwnerScope,
    ) -> CodebaseBuildPlan:
        async with self._uow_factory() as uow:
            codebase = await uow.codebase.get_by_id(codebase_id, scope=scope)
            if codebase is None:
                raise NotFoundError("codebase not found in owner scope")

            active = await uow.resource_governance.get_active_build(
                ResourceKind.CODEBASE,
                codebase_id,
            )
            if active is not None:
                version = await uow.codebase_version.get_version(
                    active.version_id,
                    codebase_id=codebase_id,
                )
                if version is None:
                    version = CodebaseVersion(
                        id=active.version_id,
                        codebase_id=codebase_id,
                        parent_version_id=active.parent_version_id,
                        build_id=active.id,
                        state=CodebaseVersionState.BUILDING,
                    )
                    version = await uow.codebase_version.add_version(version)
                return CodebaseBuildPlan(
                    version=version,
                    build=active,
                    existing=True,
                )

            version_id = str(uuid.uuid4())
            build_id = str(uuid.uuid4())
            version = CodebaseVersion(
                id=version_id,
                codebase_id=codebase_id,
                parent_version_id=codebase.active_version_id,
                build_id=build_id,
                state=CodebaseVersionState.BUILDING,
            )
            build = ResourceBuild(
                id=build_id,
                resource_kind=ResourceKind.CODEBASE,
                resource_id=codebase_id,
                version_id=version_id,
                parent_version_id=codebase.active_version_id,
                command_key=f"reanalyze:{codebase_id}",
                state=BuildState.QUEUED,
                created_by=actor_id,
            )
            version = await uow.codebase_version.add_version(version)
            build = await uow.resource_governance.add_build(build)
            return CodebaseBuildPlan(version=version, build=build)
