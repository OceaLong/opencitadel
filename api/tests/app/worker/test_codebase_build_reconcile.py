#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared ResourceBuild reconciliation is the codebase worker authority.

Mirrors tests/app/worker/test_kb_build_reconciliation.py for the codebase
kind: a candidate build left RUNNING/QUEUED by a dead worker must be
terminalized to FAILED, the codebase's ingest_task_id released, and its
status rolled back to the previously-active version — but only when no
worker still holds the task lease.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

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
from app.worker.main import AgentWorker


def test_worker_exposes_stale_codebase_build_reconciliation():
    assert callable(
        getattr(AgentWorker, "_reconcile_stale_codebase_builds", None)
    )


class _ReconcileStore:
    def __init__(self):
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        self.build = ResourceBuild(
            id="build-1",
            resource_kind=ResourceKind.CODEBASE,
            resource_id="cb-1",
            version_id="cbv2",
            parent_version_id="cbv1",
            command_key="stale",
            state=BuildState.RUNNING,
            created_by="owner",
            created_at=old,
        )
        self.version = CodebaseVersion(
            id="cbv2",
            codebase_id="cb-1",
            parent_version_id="cbv1",
            build_id="build-1",
            state=CodebaseVersionState.BUILDING,
        )
        self.codebase = Codebase(
            id="cb-1",
            name="cb",
            active_version_id="cbv1",
            ingest_task_id="build-1",
            status=CodebaseStatus.ANALYZING,
            owner_user_id="owner",
        )
        self.events: list = []


class _ReconcileGovernance:
    def __init__(self, store):
        self.store = store

    async def list_stale_builds(self, kind, *, stale_before, limit):
        assert kind is ResourceKind.CODEBASE
        assert stale_before.tzinfo is not None
        assert limit == 100
        return [self.store.build]

    async def get_build(self, build_id, *, for_update=False):
        del for_update
        return self.store.build if build_id == "build-1" else None

    async def append_event(self, build_id, event):
        assert build_id == self.store.build.id
        self.store.events.append(event)
        self.store.build = self.store.build.model_copy(
            update={"state": event.state}
        )
        return len(self.store.events)


class _ReconcileVersions:
    def __init__(self, store):
        self.store = store

    async def get_version(self, version_id, *, codebase_id=None):
        if self.store.version.id != version_id:
            return None
        if codebase_id and self.store.version.codebase_id != codebase_id:
            return None
        return self.store.version

    async def mark_failed(self, version_id, *, error):
        assert version_id == self.store.version.id
        self.store.version = self.store.version.model_copy(
            update={"state": CodebaseVersionState.FAILED}
        )
        return self.store.version


class _ReconcileCodebases:
    def __init__(self, store):
        self.store = store

    async def get_by_id(self, codebase_id, scope=None):
        del scope
        return self.store.codebase if codebase_id == "cb-1" else None

    async def save(self, codebase):
        self.store.codebase = codebase


class _ReconcileUow:
    def __init__(self, store):
        self.resource_governance = _ReconcileGovernance(store)
        self.codebase_version = _ReconcileVersions(store)
        self.codebase = _ReconcileCodebases(store)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False


@pytest.mark.asyncio
async def test_stale_codebase_build_is_failed_and_codebase_released(
    monkeypatch,
):
    store = _ReconcileStore()

    @asynccontextmanager
    async def uow_factory():
        yield _ReconcileUow(store)

    async def no_lease(_build_id):
        return None

    monkeypatch.setattr("app.worker.main.get_uow", uow_factory)
    monkeypatch.setattr("app.worker.main.get_task_lease_owner", no_lease)

    worker = object.__new__(AgentWorker)

    await worker._reconcile_stale_codebase_builds("test")
    # A second pass must be a no-op (idempotent reconciliation).
    await worker._reconcile_stale_codebase_builds("duplicate")

    assert store.build.state is BuildState.FAILED
    assert store.version.state is CodebaseVersionState.FAILED
    assert store.codebase.ingest_task_id is None
    assert store.codebase.status is CodebaseStatus.READY
    assert len(store.events) == 1
    assert store.events[0].payload["error_code"] == "BUILD_STALE"


class _PublishRaceStore:
    """A build caught between transaction A (publish_candidate + codebase
    made ready) and transaction B (the terminal build event) -- the
    candidate/codebase already reflect a successful publish, but
    build.state is still RUNNING because transaction B has not run yet."""

    def __init__(self):
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        now = datetime.now(timezone.utc)
        self.build = ResourceBuild(
            id="build-1",
            resource_kind=ResourceKind.CODEBASE,
            resource_id="cb-1",
            version_id="cbv2",
            parent_version_id="cbv1",
            command_key="stale",
            state=BuildState.RUNNING,
            created_by="owner",
            created_at=old,
        )
        self.version = CodebaseVersion(
            id="cbv2",
            codebase_id="cb-1",
            parent_version_id="cbv1",
            build_id="build-1",
            state=CodebaseVersionState.READY,
            published_at=now,
            capabilities={"lexical_search": True},
            metrics={"file_count": 3},
        )
        self.codebase = Codebase(
            id="cb-1",
            name="cb",
            # Transaction A already flipped these before committing.
            active_version_id="cbv2",
            ingest_task_id=None,
            status=CodebaseStatus.READY,
            owner_user_id="owner",
        )
        self.events: list = []


@pytest.mark.asyncio
async def test_stale_reconcile_does_not_clobber_a_published_build(
    monkeypatch,
):
    """Reconciling a build that lands in the publish two-phase-commit
    window must not downgrade an already-successful publish to FAILED --
    it must arbitrate the authoritative terminal success event instead."""
    store = _PublishRaceStore()

    @asynccontextmanager
    async def uow_factory():
        yield _ReconcileUow(store)

    async def no_lease(_build_id):
        return None

    monkeypatch.setattr("app.worker.main.get_uow", uow_factory)
    monkeypatch.setattr("app.worker.main.get_task_lease_owner", no_lease)

    worker = object.__new__(AgentWorker)

    await worker._reconcile_stale_codebase_builds("test")
    # A second pass must be idempotent too (build is now terminal).
    await worker._reconcile_stale_codebase_builds("duplicate")

    assert store.build.state is BuildState.SUCCEEDED
    assert store.version.state is CodebaseVersionState.READY
    assert store.codebase.status is CodebaseStatus.READY
    assert store.codebase.active_version_id == "cbv2"
    assert len(store.events) == 1
    assert store.events[0].phase == "publish"
    assert store.events[0].payload["reconciled_after_publish"] is True


@pytest.mark.asyncio
async def test_live_lease_codebase_build_is_untouched(monkeypatch):
    store = _ReconcileStore()

    @asynccontextmanager
    async def uow_factory():
        yield _ReconcileUow(store)

    async def has_lease(_build_id):
        return "worker-2"

    monkeypatch.setattr("app.worker.main.get_uow", uow_factory)
    monkeypatch.setattr("app.worker.main.get_task_lease_owner", has_lease)

    worker = object.__new__(AgentWorker)

    await worker._reconcile_stale_codebase_builds("test")

    assert store.build.state is BuildState.RUNNING
    assert store.codebase.ingest_task_id == "build-1"
    assert store.events == []
