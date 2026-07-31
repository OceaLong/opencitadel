#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Transactional resource-build event service contracts."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.application.errors.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.application.services.resource_build_service import ResourceBuildService
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope


class _Store:
    def __init__(self) -> None:
        self.build = ResourceBuild(
            id="build-1",
            resource_kind=ResourceKind.KNOWLEDGE_BASE,
            resource_id="kb-1",
            version_id="kb-1-v2",
            command_key="reindex:kb-1",
            created_by="owner",
        )
        self.events: list[ResourceBuildEvent] = []
        self.locked = 0
        self.commits = 0
        self.rollbacks = 0
        self.fail_append = False


class _BuildRepository:
    def __init__(self, store: _Store) -> None:
        self.store = store

    async def get_build(self, build_id: str, *, for_update: bool = False):
        if build_id != self.store.build.id:
            return None
        if for_update:
            self.store.locked += 1
        return self.store.build

    async def append_event(
        self,
        build_id: str,
        event: ResourceBuildEvent,
    ) -> int:
        if self.store.fail_append:
            raise RuntimeError("injected insert failure")
        seq = self.store.build.last_event_seq + 1
        stored = event.model_copy(update={"seq": seq})
        self.store.events.append(stored)
        self.store.build = self.store.build.model_copy(
            update={
                "state": stored.state,
                "phase": stored.phase,
                "progress": stored.progress,
                "heartbeat_at": stored.created_at,
                "last_event_seq": seq,
                "started_at": (
                    self.store.build.started_at
                    or (
                        stored.created_at
                        if stored.state == BuildState.RUNNING
                        else None
                    )
                ),
                "finished_at": (
                    stored.created_at
                    if stored.state
                    in {
                        BuildState.SUCCEEDED,
                        BuildState.DEGRADED,
                        BuildState.FAILED,
                        BuildState.CANCELLED,
                    }
                    else None
                ),
            }
        )
        return seq

    async def get_event(self, build_id: str, seq: int):
        return next(
            (
                event
                for event in self.store.events
                if event.build_id == build_id and event.seq == seq
            ),
            None,
        )

    async def list_events(self, build_id: str, after_seq: int, limit: int):
        return [
            event
            for event in self.store.events
            if event.build_id == build_id and event.seq > after_seq
        ][:limit]


class _OwnedKnowledgeBases:
    def __init__(self, *, owned: bool = True) -> None:
        self.owned = owned

    async def get_kb(self, resource_id: str, scope=None):
        if (
            self.owned
            and resource_id == "kb-1"
            and scope is not None
            and scope.user_id == "owner"
        ):
            return SimpleNamespace(
                id=resource_id,
                owner_user_id="owner",
                team_id=None,
            )
        return None


class _Codebases:
    async def get_by_id(self, _resource_id: str, scope=None):
        del scope
        return None


class _Uow:
    def __init__(self, store: _Store, *, owned: bool = True) -> None:
        self.store = store
        self.resource_governance = _BuildRepository(store)
        self.knowledge_base = _OwnedKnowledgeBases(owned=owned)
        self.codebase = _Codebases()
        self._snapshot = None

    async def __aenter__(self):
        self._snapshot = (
            self.store.build,
            deepcopy(self.store.events),
        )
        return self

    async def __aexit__(self, exc_type, _exc, _tb):
        if exc_type is not None:
            self.store.build, self.store.events = self._snapshot
            self.store.rollbacks += 1
        else:
            self.store.commits += 1
        return False


class _Notifier:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[dict[str, object]] = []

    async def publish(self, build_id: str, seq: int) -> None:
        self.messages.append({"build_id": build_id, "seq": seq})
        if self.fail:
            raise RuntimeError("redis unavailable")


def _service(
    store: _Store,
    notifier: _Notifier,
    *,
    owned: bool = True,
) -> ResourceBuildService:
    return ResourceBuildService(
        uow_factory=lambda: _Uow(store, owned=owned),
        notifier=notifier,
    )


@pytest.mark.asyncio
async def test_build_events_have_monotonic_sequence_and_commit_before_notify():
    store = _Store()
    notifier = _Notifier()
    service = _service(store, notifier)

    first = await service.append_event(
        "build-1",
        phase="parse",
        state=BuildState.RUNNING,
        progress=0.25,
        scope=OwnerScope.personal("owner"),
    )
    second = await service.append_event(
        "build-1",
        phase="index",
        state=BuildState.RUNNING,
        progress=0.75,
        scope=OwnerScope.personal("owner"),
    )

    assert (first.seq, second.seq) == (1, 2)
    assert store.locked == 2
    assert store.commits == 2
    assert notifier.messages == [
        {"build_id": "build-1", "seq": 1},
        {"build_id": "build-1", "seq": 2},
    ]
    assert store.build.last_event_seq == 2
    assert store.build.heartbeat_at == second.created_at


@pytest.mark.asyncio
async def test_list_events_replays_strictly_after_cursor_and_validates_bounds():
    store = _Store()
    service = _service(store, _Notifier())
    scope = OwnerScope.personal("owner")
    for phase, progress in (("parse", 0.2), ("index", 0.6), ("publish", 0.9)):
        await service.append_event(
            "build-1",
            phase=phase,
            state=BuildState.RUNNING,
            progress=progress,
            scope=scope,
        )

    replay = await service.list_events(
        "build-1",
        after_seq=1,
        limit=2,
        scope=scope,
    )

    assert [event.seq for event in replay] == [2, 3]
    with pytest.raises(BadRequestError, match="cursor"):
        await service.list_events(
            "build-1", after_seq=-1, limit=10, scope=scope
        )
    for invalid_limit in (0, 501):
        with pytest.raises(BadRequestError, match="limit"):
            await service.list_events(
                "build-1",
                after_seq=0,
                limit=invalid_limit,
                scope=scope,
            )


@pytest.mark.asyncio
async def test_replay_rejects_cursor_ahead_of_authoritative_build_cursor():
    store = _Store()
    service = _service(store, _Notifier())
    scope = OwnerScope.personal("owner")

    with pytest.raises(BadRequestError, match="ahead"):
        await service.list_events(
            "build-1",
            after_seq=1,
            limit=10,
            scope=scope,
        )

    await service.append_event(
        "build-1",
        phase="parse",
        state=BuildState.RUNNING,
        progress=0.1,
        scope=scope,
    )
    with pytest.raises(BadRequestError, match="ahead"):
        await service.list_events(
            "build-1",
            after_seq=2,
            limit=10,
            scope=scope,
        )


@pytest.mark.asyncio
async def test_terminal_duplicate_is_idempotent_and_conflict_is_rejected():
    store = _Store()
    notifier = _Notifier()
    service = _service(store, notifier)
    scope = OwnerScope.personal("owner")
    await service.append_event(
        "build-1",
        phase="work",
        state=BuildState.RUNNING,
        progress=0.5,
        scope=scope,
    )
    terminal = await service.append_event(
        "build-1",
        phase="publish",
        state=BuildState.SUCCEEDED,
        progress=1.0,
        payload={"version_id": "kb-1-v2"},
        scope=scope,
    )

    duplicate = await service.append_event(
        "build-1",
        phase="publish",
        state=BuildState.SUCCEEDED,
        progress=1.0,
        payload={"version_id": "kb-1-v2"},
        scope=scope,
    )

    assert duplicate == terminal
    assert [event.seq for event in store.events] == [1, 2]
    assert notifier.messages == [
        {"build_id": "build-1", "seq": 1},
        {"build_id": "build-1", "seq": 2},
    ]
    with pytest.raises(ConflictError, match="terminal"):
        await service.append_event(
            "build-1",
            phase="publish",
            state=BuildState.FAILED,
            progress=1.0,
            scope=scope,
        )


@pytest.mark.asyncio
async def test_invalid_state_transition_progress_and_resource_identity_fail_closed():
    store = _Store()
    service = _service(store, _Notifier())
    scope = OwnerScope.personal("owner")

    with pytest.raises(ConflictError, match="transition"):
        await service.append_event(
            "build-1",
            phase="publish",
            state=BuildState.SUCCEEDED,
            progress=1.0,
            scope=scope,
        )
    with pytest.raises(BadRequestError, match="resource"):
        await service.append_event(
            "build-1",
            phase="parse",
            state=BuildState.RUNNING,
            progress=0.1,
            resource_id="kb-other",
            scope=scope,
        )
    await service.append_event(
        "build-1",
        phase="parse",
        state=BuildState.RUNNING,
        progress=0.5,
        scope=scope,
    )
    with pytest.raises(BadRequestError, match="progress"):
        await service.append_event(
            "build-1",
            phase="parse",
            state=BuildState.RUNNING,
            progress=0.4,
            scope=scope,
        )


@pytest.mark.asyncio
async def test_known_kb_phase_regression_is_rejected_by_shared_service():
    store = _Store()
    service = _service(store, _Notifier())
    scope = OwnerScope.personal("owner")
    await service.append_event(
        "build-1",
        phase="graph",
        state=BuildState.RUNNING,
        progress=0.76,
        scope=scope,
    )

    with pytest.raises(BadRequestError, match="phase"):
        await service.append_event(
            "build-1",
            phase="parse",
            state=BuildState.RUNNING,
            progress=0.76,
            scope=scope,
        )


@pytest.mark.asyncio
async def test_authoritative_append_closes_orphan_without_owner_resource():
    store = _Store()
    notifier = _Notifier()
    service = _service(store, notifier, owned=False)

    event = await service.append_event_authoritative(
        "build-1",
        phase="failed",
        state=BuildState.FAILED,
        progress=0.0,
        payload={"error_code": "BUILD_CLOSURE_INVALID"},
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id="kb-1",
        version_id="kb-1-v2",
    )

    assert event.state is BuildState.FAILED
    assert store.build.state is BuildState.FAILED
    assert notifier.messages == [{"build_id": "build-1", "seq": 1}]


@pytest.mark.asyncio
async def test_foreign_and_missing_builds_share_not_found_semantics():
    store = _Store()
    scope = OwnerScope.personal("owner")
    foreign = _service(store, _Notifier(), owned=False)

    with pytest.raises(NotFoundError, match="owner scope"):
        await foreign.list_events(
            "build-1", after_seq=0, limit=10, scope=scope
        )
    with pytest.raises(NotFoundError, match="owner scope"):
        await _service(store, _Notifier()).list_events(
            "missing", after_seq=0, limit=10, scope=scope
        )


@pytest.mark.asyncio
async def test_database_failure_rolls_back_and_never_notifies():
    store = _Store()
    store.fail_append = True
    notifier = _Notifier()

    with pytest.raises(RuntimeError, match="injected"):
        await _service(store, notifier).append_event(
            "build-1",
            phase="parse",
            state=BuildState.RUNNING,
            progress=0.1,
            scope=OwnerScope.personal("owner"),
        )

    assert store.events == []
    assert store.build.last_event_seq == 0
    assert store.rollbacks == 1
    assert notifier.messages == []


@pytest.mark.asyncio
async def test_notification_failure_does_not_rollback_committed_postgres_event():
    store = _Store()
    notifier = _Notifier(fail=True)

    event = await _service(store, notifier).append_event(
        "build-1",
        phase="parse",
        state=BuildState.RUNNING,
        progress=0.1,
        scope=OwnerScope.personal("owner"),
    )

    assert event.seq == 1
    assert store.commits == 1
    assert store.rollbacks == 0
    assert [stored.seq for stored in store.events] == [1]
    assert notifier.messages == [{"build_id": "build-1", "seq": 1}]
