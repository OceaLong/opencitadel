#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.services.codebase_service import CodebaseService
from app.domain.models.codebase import Codebase, CodebaseStatus
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
from app.domain.services.codebase.version_builder import CodebaseBuildPlan


class _TaskState:
    def __init__(self):
        self.register_task = AsyncMock()


class _CodebaseRepo:
    def __init__(self):
        self.codebase = Codebase(
            id="cb1",
            name="demo",
            status=CodebaseStatus.READY,
            active_version_id="cbv1",
            owner_user_id="owner",
        )
        self.saved: list[Codebase] = []

    async def get_by_id(self, codebase_id, scope=None):
        if codebase_id != self.codebase.id:
            return None
        if scope and scope.user_id != self.codebase.owner_user_id:
            return None
        return self.codebase

    async def save(self, codebase):
        self.codebase = codebase
        self.saved.append(codebase)


class _Uow:
    def __init__(self, codebase_repo):
        self.codebase = codebase_repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _VersionService:
    def __init__(self, plans):
        self.plans = list(plans)
        self.calls = []

    async def create_reanalysis(self, codebase_id, *, actor_id, scope):
        self.calls.append((codebase_id, actor_id, scope))
        return self.plans.pop(0)


def _plan(*, existing: bool) -> CodebaseBuildPlan:
    version = CodebaseVersion(
        id="cbv2",
        codebase_id="cb1",
        parent_version_id="cbv1",
        build_id="build-1",
        state=CodebaseVersionState.BUILDING,
    )
    build = ResourceBuild(
        id="build-1",
        resource_kind=ResourceKind.CODEBASE,
        resource_id="cb1",
        version_id="cbv2",
        parent_version_id="cbv1",
        command_key="reanalyze:cb1",
        state=BuildState.QUEUED,
        created_by="owner",
    )
    return CodebaseBuildPlan(version=version, build=build, existing=existing)


@pytest.mark.asyncio
async def test_reanalyze_dispatches_new_candidate_and_dedupes_existing(monkeypatch):
    repo = _CodebaseRepo()
    version_service = _VersionService(
        [
            _plan(existing=False),
            _plan(existing=True),
        ]
    )
    service = CodebaseService(
        uow_factory=lambda: _Uow(repo),
        sandbox_cls=MagicMock(),
        file_storage=object(),  # type: ignore[arg-type]
        codebase_version_service=version_service,  # type: ignore[arg-type]
    )
    task_state = _TaskState()
    service._task_state = task_state  # type: ignore[method-assign]
    dispatch = AsyncMock()
    monkeypatch.setattr(
        "app.application.services.codebase_service.RedisStreamTask.dispatch_to_worker",
        dispatch,
    )
    scope = OwnerScope.personal("owner")

    created = await service.reanalyze("cb1", scope=scope)
    repo.codebase = repo.codebase.model_copy(
        update={
            "status": CodebaseStatus.ANALYZING,
            "error": "still running",
        }
    )
    existing = await service.reanalyze("cb1", scope=scope)

    assert created.ingest_task_id == "build-1"
    assert existing.ingest_task_id == "build-1"
    task_state.register_task.assert_awaited_once_with(
        "build-1",
        session_id="codebase-ingest:cb1",
        task_type="codebase_ingest",
        resource_id="cb1",
    )
    dispatch.assert_awaited_once()
    assert len(repo.saved) == 2
    assert repo.saved[0].status is CodebaseStatus.PENDING
    assert repo.saved[-1].status is CodebaseStatus.ANALYZING
    assert repo.saved[-1].error == "still running"
    assert version_service.calls == [
        ("cb1", "owner", scope),
        ("cb1", "owner", scope),
    ]
