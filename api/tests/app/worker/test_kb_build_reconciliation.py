#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared ResourceBuild reconciliation is the KB worker authority."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.domain.models.knowledge_base import KBStatus, KnowledgeBase
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
)
from app.domain.repositories.resource_governance_repository import (
    ResourceGovernanceRepository,
)
from app.worker.main import AgentWorker


def test_worker_exposes_shared_kb_build_reconciliation():
    assert callable(getattr(AgentWorker, "_reconcile_stale_kb_builds", None))
    assert callable(
        getattr(ResourceGovernanceRepository, "list_stale_builds", None)
    )


class _ReconcileStore:
    def __init__(self):
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        self.build = ResourceBuild(
            id="build-1",
            resource_kind=ResourceKind.KNOWLEDGE_BASE,
            resource_id="kb-1",
            version_id="v2",
            parent_version_id="v1",
            command_key="stale",
            created_by="owner",
            created_at=old,
        )
        self.version = KnowledgeBaseVersion(
            id="v2",
            knowledge_base_id="kb-1",
            parent_version_id="v1",
            build_id="build-1",
        )
        self.kb = KnowledgeBase(
            id="kb-1",
            name="KB",
            active_version_id="v1",
            ingest_task_id="build-1",
            status=KBStatus.PENDING,
            owner_user_id="owner",
        )
        self.events: list[ResourceBuildEvent] = []
        self.missing_candidate = False
        self.missing_kb = False


class _ReconcileRepos:
    def __init__(self, store):
        self.store = store

    async def list_stale_builds(
        self,
        kind,
        *,
        stale_before,
        limit,
    ):
        assert kind is ResourceKind.KNOWLEDGE_BASE
        assert stale_before.tzinfo is not None
        assert limit == 100
        return [self.store.build]

    async def get_build(self, build_id, *, for_update=False):
        del for_update
        return self.store.build if build_id == "build-1" else None


class _ReconcileVersions:
    def __init__(self, store):
        self.store = store

    async def get_build_candidate(self, build_id):
        assert build_id == "build-1"
        return self.store.version, []

    async def get_version(self, version_id, *, knowledge_base_id):
        assert (version_id, knowledge_base_id) == ("v2", "kb-1")
        if self.store.missing_candidate:
            return None
        return self.store.version

    async def fail_candidate(
        self,
        version_id,
        *,
        knowledge_base_id,
        metrics,
    ):
        assert (version_id, knowledge_base_id) == ("v2", "kb-1")
        if self.store.version.state is not KnowledgeVersionState.BUILDING:
            return False
        self.store.version = self.store.version.model_copy(
            update={
                "state": KnowledgeVersionState.FAILED,
                "metrics": metrics,
            }
        )
        return True


class _ReconcileKbs:
    def __init__(self, store):
        self.store = store

    async def get_kb(self, kb_id, scope=None):
        del scope
        if self.store.missing_kb:
            return None
        return self.store.kb if kb_id == "kb-1" else None

    async def save_kb(self, kb):
        self.store.kb = kb


class _ReconcileUow:
    def __init__(self, store):
        self.resource_governance = _ReconcileRepos(store)
        self.knowledge_version = _ReconcileVersions(store)
        self.knowledge_base = _ReconcileKbs(store)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False


class _ReconcileBuildService:
    def __init__(self, store):
        self.store = store

    async def require_build(self, build_id, scope):
        del scope
        assert build_id == "build-1"
        return self.store.build

    async def append_event(self, build_id, **values):
        assert build_id == "build-1"
        if self.store.build.state in {
            BuildState.FAILED,
            BuildState.CANCELLED,
            BuildState.SUCCEEDED,
            BuildState.DEGRADED,
        }:
            return self.store.events[-1]
        event = ResourceBuildEvent(
            build_id=build_id,
            seq=len(self.store.events) + 1,
            phase=values["phase"],
            state=values["state"],
            progress=values["progress"],
            payload=values["payload"],
        )
        self.store.events.append(event)
        self.store.build = self.store.build.model_copy(
            update={
                "state": event.state,
                "phase": event.phase,
                "progress": event.progress,
                "last_event_seq": event.seq,
            }
        )
        return event

    async def append_event_authoritative(self, build_id, **values):
        return await self.append_event(build_id, **values)


@pytest.mark.asyncio
async def test_stale_reconciliation_is_idempotent_and_preserves_old_active(
    monkeypatch,
):
    store = _ReconcileStore()

    @asynccontextmanager
    async def uow_factory():
        yield _ReconcileUow(store)

    async def no_lease(_build_id):
        return None

    monkeypatch.setattr(
        "app.worker.main.get_uow",
        uow_factory,
    )
    monkeypatch.setattr(
        "app.worker.main.get_task_lease_owner",
        no_lease,
    )
    monkeypatch.setattr(
        "app.worker.main.get_worker_container",
        lambda: SimpleNamespace(
            resource_build_service=lambda: _ReconcileBuildService(store)
        ),
    )
    worker = object.__new__(AgentWorker)

    await worker._reconcile_stale_kb_builds("test")
    await worker._reconcile_stale_kb_builds("duplicate")

    assert store.kb.active_version_id == "v1"
    assert store.kb.status is KBStatus.READY
    assert store.version.state is KnowledgeVersionState.FAILED
    assert store.build.state is BuildState.FAILED
    assert len(store.events) == 1
    assert store.events[0].payload["error_code"] == "BUILD_STALE"


@pytest.mark.asyncio
async def test_stale_reconciliation_repairs_publish_event_without_failing(
    monkeypatch,
):
    store = _ReconcileStore()
    store.kb.active_version_id = "v2"
    store.version = store.version.model_copy(
        update={
            "state": KnowledgeVersionState.DEGRADED,
            "capabilities": {
                "keyword_search": True,
                "vector_search": False,
                "graph_search": False,
            },
            "degraded_reasons": ["EMBEDDING_UNAVAILABLE"],
            "metrics": {"child_chunk_count": 1},
            "published_at": datetime.now(timezone.utc),
        }
    )

    @asynccontextmanager
    async def uow_factory():
        yield _ReconcileUow(store)

    async def no_lease(_build_id):
        return None

    monkeypatch.setattr("app.worker.main.get_uow", uow_factory)
    monkeypatch.setattr(
        "app.worker.main.get_task_lease_owner",
        no_lease,
    )
    monkeypatch.setattr(
        "app.worker.main.get_worker_container",
        lambda: SimpleNamespace(
            resource_build_service=lambda: _ReconcileBuildService(store)
        ),
    )
    worker = object.__new__(AgentWorker)

    await worker._reconcile_stale_kb_builds("repair")

    assert store.version.state is KnowledgeVersionState.DEGRADED
    assert store.kb.active_version_id == "v2"
    assert store.build.state is BuildState.DEGRADED
    assert len(store.events) == 1
    assert store.events[0].phase == "publish"
    assert store.events[0].payload["reconciled_after_publish"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["candidate", "kb"])
async def test_stale_orphan_is_authoritatively_closed_once(
    monkeypatch,
    missing,
):
    store = _ReconcileStore()
    if missing == "candidate":
        store.missing_candidate = True
    else:
        store.missing_kb = True

    @asynccontextmanager
    async def uow_factory():
        yield _ReconcileUow(store)

    async def no_lease(_build_id):
        return None

    monkeypatch.setattr("app.worker.main.get_uow", uow_factory)
    monkeypatch.setattr(
        "app.worker.main.get_task_lease_owner",
        no_lease,
    )
    monkeypatch.setattr(
        "app.worker.main.get_worker_container",
        lambda: SimpleNamespace(
            resource_build_service=lambda: _ReconcileBuildService(store)
        ),
    )
    worker = object.__new__(AgentWorker)

    await worker._reconcile_stale_kb_builds("orphan")
    await worker._reconcile_stale_kb_builds("duplicate")

    assert store.build.state is BuildState.FAILED
    assert len(store.events) == 1
    assert store.events[0].payload["error_code"] == "BUILD_STALE"
