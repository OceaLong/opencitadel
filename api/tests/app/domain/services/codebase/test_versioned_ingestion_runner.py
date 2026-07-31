#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.codebase import Codebase, CodebaseStatus
from app.domain.models.codebase_version import (
    CodebaseVersion,
    CodebaseVersionState,
)
from app.domain.models.event import ErrorEvent
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceKind,
)
from app.domain.services.codebase.ingestion_runner import (
    CodebaseIngestionRunner,
    SourceCollectionResult,
)


class _CodebaseRepo:
    def __init__(self):
        self.codebase = Codebase(
            id="cb1",
            status=CodebaseStatus.READY,
            active_version_id="cbv1",
            owner_user_id="owner",
        )

    async def get_by_id(self, codebase_id, scope=None):
        return self.codebase if codebase_id == "cb1" else None

    async def save(self, codebase):
        self.codebase = codebase

    async def update_status(self, codebase_id, status, error=None):
        self.codebase.status = status
        self.codebase.error = error

    async def clear_analysis_data(self, codebase_id):
        raise AssertionError("versioned build must not clear shared active rows")


class _VersionRepo:
    def __init__(self):
        self.versions = {
            "cbv1": CodebaseVersion(
                id="cbv1",
                codebase_id="cb1",
                state=CodebaseVersionState.READY,
                published_at=datetime.now(timezone.utc),
            ),
            "cbv2": CodebaseVersion(
                id="cbv2",
                codebase_id="cb1",
                parent_version_id="cbv1",
                state=CodebaseVersionState.BUILDING,
                build_id="build-1",
            ),
        }

    async def get_version(self, version_id, *, codebase_id=None):
        version = self.versions.get(version_id)
        if version and codebase_id and version.codebase_id != codebase_id:
            return None
        return version

    async def mark_failed(self, version_id, *, error):
        version = self.versions[version_id]
        self.versions[version_id] = version.model_copy(
            update={"state": CodebaseVersionState.FAILED}
        )
        return self.versions[version_id]

    async def update_snapshot(
        self,
        version_id,
        *,
        source_snapshot_key,
        source_revision,
        source_digest,
    ):
        version = self.versions[version_id]
        self.versions[version_id] = version.model_copy(
            update={
                "source_snapshot_key": source_snapshot_key,
                "source_revision": source_revision,
                "source_digest": source_digest,
            }
        )
        return self.versions[version_id]


class _BuildRepo:
    def __init__(self):
        self.build = ResourceBuild(
            id="build-1",
            resource_kind=ResourceKind.CODEBASE,
            resource_id="cb1",
            version_id="cbv2",
            parent_version_id="cbv1",
            command_key="reanalyze:cb1",
            state=BuildState.QUEUED,
            created_by="owner",
        )

    async def get_build(self, build_id, *, for_update=False):
        return self.build if build_id == self.build.id else None


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
async def test_build_failure_keeps_active_version_and_marks_candidate_failed(monkeypatch):
    codebase = _CodebaseRepo()
    versions = _VersionRepo()
    builds = _BuildRepo()
    runner = CodebaseIngestionRunner(
        uow_factory=lambda: _Uow(codebase, versions, builds),
        sandbox_cls=MagicMock(),
        file_storage=MagicMock(),
    )
    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.CodebaseIngestionRunner._materialize",
        AsyncMock(side_effect=RuntimeError("materialize failed")),
    )

    with pytest.raises(RuntimeError, match="materialize failed"):
        async for _event in runner.run_build("build-1"):
            pass

    assert codebase.codebase.active_version_id == "cbv1"
    assert versions.versions["cbv2"].state is CodebaseVersionState.FAILED


@pytest.mark.asyncio
async def test_empty_indexable_source_fails_with_stable_error_code(monkeypatch):
    codebase = _CodebaseRepo()
    versions = _VersionRepo()
    builds = _BuildRepo()
    runner = CodebaseIngestionRunner(
        uow_factory=lambda: _Uow(codebase, versions, builds),
        sandbox_cls=MagicMock(),
        file_storage=MagicMock(),
    )
    sandbox = MagicMock(id="sandbox-1")
    sandbox.create_workspace_snapshot = AsyncMock(return_value=b"snapshot")
    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.CodebaseIngestionRunner._materialize",
        AsyncMock(return_value=(sandbox, "/home/ubuntu/codebase")),
    )
    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.CodebaseIngestionRunner._collect_source_files",
        AsyncMock(
            return_value=SourceCollectionResult(
                entries=[],
                scanned=12,
                skipped=12,
                failed=0,
                truncated=False,
                total_bytes=0,
            )
        ),
    )

    events = []
    with pytest.raises(RuntimeError, match="CODEBASE_NO_INDEXABLE_SOURCE"):
        async for event in runner.run_build("build-1"):
            events.append(event)

    assert any(
        isinstance(event, ErrorEvent)
        and event.code == "CODEBASE_NO_INDEXABLE_SOURCE"
        for event in events
    )
    assert codebase.codebase.active_version_id == "cbv1"
    assert versions.versions["cbv2"].state is CodebaseVersionState.FAILED
