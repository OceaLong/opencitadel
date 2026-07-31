#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timezone

import pytest

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.application.services.codebase_version_service import CodebaseVersionService
from app.domain.models.codebase import Codebase, CodebaseStatus
from app.domain.models.codebase_version import (
    CodebaseVersion,
    CodebaseVersionState,
)
from app.domain.models.resource_governance import BuildState, ResourceKind
from app.domain.models.scope import OwnerScope


class _CodebaseRepo:
    def __init__(self):
        self.codebases = {
            "cb1": Codebase(
                id="cb1",
                status=CodebaseStatus.READY,
                owner_user_id="owner",
                active_version_id="ready-v1",
            ),
            "other": Codebase(
                id="other",
                status=CodebaseStatus.READY,
                owner_user_id="owner",
                active_version_id="other-v1",
            ),
        }

    async def get_by_id(self, codebase_id, scope=None):
        codebase = self.codebases.get(codebase_id)
        if codebase and scope and codebase.owner_user_id != scope.user_id:
            return None
        return codebase


class _VersionRepo:
    def __init__(self):
        self.versions = {
            "ready-v1": CodebaseVersion(
                id="ready-v1",
                codebase_id="cb1",
                state=CodebaseVersionState.READY,
                published_at=datetime.now(timezone.utc),
                capabilities={"lexical_search": True, "vector_search": True},
            ),
            "degraded-v2": CodebaseVersion(
                id="degraded-v2",
                codebase_id="cb1",
                state=CodebaseVersionState.DEGRADED,
                published_at=datetime.now(timezone.utc),
                capabilities={"lexical_search": True, "vector_search": False},
                degraded_reasons=["EMBEDDING_UNAVAILABLE"],
            ),
            "building-v3": CodebaseVersion(
                id="building-v3",
                codebase_id="cb1",
                state=CodebaseVersionState.BUILDING,
            ),
            "foreign-v1": CodebaseVersion(
                id="foreign-v1",
                codebase_id="other",
                state=CodebaseVersionState.READY,
                published_at=datetime.now(timezone.utc),
            ),
        }

    async def get_version(self, version_id, *, codebase_id=None):
        version = self.versions.get(version_id)
        if version and codebase_id and version.codebase_id != codebase_id:
            return None
        return version

    async def list_versions(self, codebase_id, *, limit=500, before=None):
        return [
            version
            for version in self.versions.values()
            if version.codebase_id == codebase_id
        ]


class _Uow:
    def __init__(self):
        self.codebase = _CodebaseRepo()
        self.codebase_version = _VersionRepo()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_resolve_active_and_explicit_published_codebase_versions():
    service = CodebaseVersionService(uow_factory=_Uow)
    scope = OwnerScope.personal("owner")

    active = await service.resolve_published_version("cb1", None, scope)
    degraded = await service.resolve_published_version("cb1", "degraded-v2", scope)

    assert active.resource_kind is ResourceKind.CODEBASE
    assert active.version_id == "ready-v1"
    assert active.state is BuildState.SUCCEEDED
    assert degraded.version_id == "degraded-v2"
    assert degraded.state is BuildState.DEGRADED
    assert degraded.degraded_reasons == ["EMBEDDING_UNAVAILABLE"]


@pytest.mark.asyncio
async def test_provider_rejects_building_or_foreign_versions():
    service = CodebaseVersionService(uow_factory=_Uow)
    scope = OwnerScope.personal("owner")

    with pytest.raises(BadRequestError):
        await service.resolve_published_version("cb1", "building-v3", scope)
    with pytest.raises(NotFoundError):
        await service.resolve_published_version("cb1", "foreign-v1", scope)


@pytest.mark.asyncio
async def test_list_published_versions_filters_unpublished():
    service = CodebaseVersionService(uow_factory=_Uow)

    versions = await service.list_published_versions(
        "cb1",
        OwnerScope.personal("owner"),
    )

    assert [version.version_id for version in versions] == [
        "ready-v1",
        "degraded-v2",
    ]
