#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.models.codebase import Codebase, CodebaseStatus
from app.domain.models.codebase_version import CodebaseVersionState
from app.domain.models.resource_governance import BuildState, ResourceKind
from app.domain.models.scope import OwnerScope
from app.domain.services.codebase.version_builder import CodebaseVersionBuilder


class _CodebaseRepo:
    def __init__(self):
        self.codebase = Codebase(
            id="cb1",
            status=CodebaseStatus.READY,
            owner_user_id="owner",
            active_version_id="cbv1",
        )

    async def get_by_id(self, codebase_id, scope=None):
        if codebase_id != self.codebase.id:
            return None
        if scope and self.codebase.owner_user_id != scope.user_id:
            return None
        return self.codebase


class _VersionRepo:
    def __init__(self, builds):
        self.versions = {}
        self.builds = builds

    async def add_version(self, version):
        if version.build_id not in self.builds.builds:
            raise RuntimeError("version build foreign key is missing")
        self.versions[version.id] = version
        return version

    async def get_version(self, version_id, *, codebase_id=None):
        version = self.versions.get(version_id)
        if version and codebase_id and version.codebase_id != codebase_id:
            return None
        return version


class _BuildRepo:
    def __init__(self):
        self.builds = {}
        self.active = None

    async def get_active_build(self, resource_kind, resource_id):
        return self.active

    async def add_build(self, build):
        self.builds[build.id] = build
        self.active = build
        return build


class _Uow:
    def __init__(self, codebase, versions, builds):
        self.codebase = codebase
        self.codebase_version = versions
        self.resource_governance = builds

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_duplicate_reanalyze_returns_same_candidate_build():
    codebase = _CodebaseRepo()
    builds = _BuildRepo()
    versions = _VersionRepo(builds)
    builder = CodebaseVersionBuilder(
        lambda: _Uow(codebase, versions, builds)
    )
    scope = OwnerScope.personal("owner")

    first = await builder.create_reanalysis("cb1", actor_id="owner", scope=scope)
    second = await builder.create_reanalysis("cb1", actor_id="owner", scope=scope)

    assert first.build.id == second.build.id
    assert first.version.id == second.version.id
    assert first.existing is False
    assert second.existing is True
    assert first.build.resource_kind is ResourceKind.CODEBASE
    assert first.build.resource_id == "cb1"
    assert first.build.version_id == first.version.id
    assert first.build.parent_version_id == "cbv1"
    assert first.build.state is BuildState.QUEUED
    assert first.version.state is CodebaseVersionState.BUILDING
    assert first.version.parent_version_id == "cbv1"
