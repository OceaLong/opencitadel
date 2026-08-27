"""Create codebase analysis candidate versions and durable builds."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.domain.errors import NotFoundError
from app.domain.models.codebase_version import (
    CodebaseVersion,
    CodebaseVersionState,
)
from app.domain.models.resource_bindings import (
    ResourceBuildIntent,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork


@dataclass(frozen=True)
class CodebaseBuildPlan:
    version: CodebaseVersion
    build: ResourceBuildIntent
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
        before_commit: Callable[[IUnitOfWork, CodebaseBuildPlan], Awaitable[None]] | None = None,
    ) -> CodebaseBuildPlan:
        async with self._uow_factory() as uow:
            codebase = await uow.codebase.get_by_id(codebase_id, scope=scope)
            if codebase is None:
                raise NotFoundError("codebase not found in owner scope")

            active = await uow.codebase_version.get_active_candidate(codebase_id)
            if active is not None:
                return CodebaseBuildPlan(
                    version=active,
                    build=ResourceBuildIntent(
                        build_id=active.build_id,
                        resource_kind=ResourceKind.CODEBASE,
                        resource_id=codebase_id,
                        version_id=active.id,
                        parent_version_id=active.parent_version_id,
                    ),
                    existing=True,
                )

            version_id = str(uuid.uuid4())
            build_id = str(uuid.uuid4())
            version = CodebaseVersion(
                id=version_id,
                codebase_id=codebase_id,
                parent_version_id=codebase.active_version_id,
                build_id=build_id,
                request_key=_reanalysis_request_key(codebase_id),
                state=CodebaseVersionState.BUILDING,
            )
            build = ResourceBuildIntent(
                build_id=build_id,
                resource_kind=ResourceKind.CODEBASE,
                resource_id=codebase_id,
                version_id=version_id,
                parent_version_id=codebase.active_version_id,
            )
            version = await uow.codebase_version.add_version(version)
            plan = CodebaseBuildPlan(version=version, build=build)
            if before_commit is not None:
                await before_commit(uow, plan)
            await uow.commit()
            return plan


def _reanalysis_request_key(codebase_id: str) -> str:
    import hashlib

    return hashlib.sha256(f"reanalyze:{codebase_id}".encode()).hexdigest()
