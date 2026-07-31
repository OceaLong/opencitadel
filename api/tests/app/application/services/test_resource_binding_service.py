#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.application.errors.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.application.services.resource_binding_service import (
    ResourceBindingService,
)
from app.domain.models.resource_governance import (
    BuildState,
    PublishedResourceVersion,
    ResourceKind,
    SessionResourceBinding,
)
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.scope import OwnerScope
from app.domain.services.resource_version_provider import (
    ResourceVersionProviderRegistry,
)


class _FakeProvider:
    def __init__(
        self,
        kind: ResourceKind,
        response: PublishedResourceVersion,
    ) -> None:
        self.kind = kind
        self.response = response
        self.calls = []
        self.release: asyncio.Event | None = None
        self.after_resolve = None

    async def resolve_published_version(
        self,
        resource_id: str,
        requested_version_id: str | None,
        scope: OwnerScope,
    ) -> PublishedResourceVersion:
        self.calls.append((resource_id, requested_version_id, scope))
        if self.release is not None:
            await self.release.wait()
        response = self.response
        if self.after_resolve is not None:
            outcome = self.after_resolve(response)
            if asyncio.iscoroutine(outcome):
                await outcome
        return response


class _Store:
    def __init__(self) -> None:
        self.sessions = {
            "s1": SimpleNamespace(
                id="s1",
                owner_user_id="u1",
                team_id=None,
            )
        }
        self.bindings: list[SessionResourceBinding] = []
        self.session_locks: dict[str, asyncio.Lock] = {}
        self.kb_locks: dict[str, asyncio.Lock] = {}
        self.kb_lock_calls: list[str] = []
        self.version_rechecks: list[tuple[str, str]] = []
        self.deleted_versions: set[str] = set()
        self.fail_after_deactivate = False


def _in_scope(session, scope: OwnerScope) -> bool:
    if scope.team_id is not None:
        return session.team_id == scope.team_id
    return session.owner_user_id == scope.user_id and session.team_id is None


class _SessionRepository:
    def __init__(self, store: _Store, uow: "_Uow") -> None:
        self._store = store
        self._uow = uow

    async def get_metadata(self, session_id: str, scope=None):
        session = self._store.sessions.get(session_id)
        if session is None:
            return None
        if scope is not None and not _in_scope(session, scope):
            return None
        return session

    async def lock_by_id(self, session_id: str, scope=None):
        lock = self._store.session_locks.setdefault(
            session_id,
            asyncio.Lock(),
        )
        await lock.acquire()
        self._uow._held_locks.append(lock)
        return await self.get_metadata(session_id, scope=scope)


class _GovernanceRepository:
    def __init__(self, store: _Store) -> None:
        self._store = store

    async def get_current_binding(
        self,
        session_id: str,
        resource_kind: ResourceKind,
        *,
        for_update: bool = False,
    ):
        del for_update
        matches = [
            binding
            for binding in self._store.bindings
            if binding.session_id == session_id
            and binding.resource_kind == resource_kind
            and binding.is_current
        ]
        assert len(matches) <= 1
        return matches[0] if matches else None

    async def list_bindings(
        self,
        session_id: str,
        resource_kind: ResourceKind | None = None,
    ):
        return [
            binding
            for binding in self._store.bindings
            if binding.session_id == session_id
            and (
                resource_kind is None
                or binding.resource_kind == resource_kind
            )
        ]

    async def list_current_bindings(self, session_id: str):
        return [
            binding
            for binding in self._store.bindings
            if binding.session_id == session_id and binding.is_current
        ]

    async def add_binding(
        self,
        binding: SessionResourceBinding,
    ) -> SessionResourceBinding:
        if any(
            existing.session_id == binding.session_id
            and existing.resource_kind == binding.resource_kind
            and existing.is_current
            for existing in self._store.bindings
        ):
            raise RuntimeError("current binding uniqueness violated")
        self._store.bindings.append(binding)
        return binding

    async def replace_current_binding(
        self,
        current: SessionResourceBinding,
        replacement: SessionResourceBinding,
    ) -> SessionResourceBinding:
        index = self._store.bindings.index(current)
        self._store.bindings[index] = current.model_copy(
            update={"is_current": False}
        )
        if self._store.fail_after_deactivate:
            raise RuntimeError("injected insert failure")
        self._store.bindings.append(replacement)
        return replacement


class _KnowledgeBaseRepository:
    def __init__(self, store: _Store, uow: "_Uow") -> None:
        self._store = store
        self._uow = uow

    async def get_kb_for_update(self, kb_id: str, scope=None):
        del scope
        lock = self._store.kb_locks.setdefault(kb_id, asyncio.Lock())
        await lock.acquire()
        self._uow._held_locks.append(lock)
        self._store.kb_lock_calls.append(kb_id)
        return SimpleNamespace(id=kb_id)


class _KnowledgeVersionRepository:
    def __init__(self, store: _Store) -> None:
        self._store = store

    async def get_version(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
    ):
        self._store.version_rechecks.append(
            (knowledge_base_id, version_id)
        )
        if version_id in self._store.deleted_versions:
            return None
        now = datetime.now(timezone.utc)
        return KnowledgeBaseVersion(
            id=version_id,
            knowledge_base_id=knowledge_base_id,
            state=KnowledgeVersionState.READY,
            created_at=now,
            published_at=now,
        )


class _Uow:
    def __init__(self, store: _Store) -> None:
        self._store = store
        self._snapshot = None
        self._held_locks: list[asyncio.Lock] = []
        self.session = _SessionRepository(store, self)
        self.resource_governance = _GovernanceRepository(store)
        self.knowledge_base = _KnowledgeBaseRepository(store, self)
        self.knowledge_version = _KnowledgeVersionRepository(store)

    async def __aenter__(self):
        self._snapshot = deepcopy(self._store.bindings)
        return self

    async def __aexit__(self, exc_type, _exc, _tb):
        if exc_type is not None:
            self._store.bindings = self._snapshot
        for lock in reversed(self._held_locks):
            lock.release()
        return False


@pytest.fixture
def scope() -> OwnerScope:
    return OwnerScope.personal("u1")


@pytest.fixture
def store() -> _Store:
    return _Store()


def _published(
    kind: ResourceKind,
    resource_id: str,
    version_id: str,
    *,
    degraded: bool = False,
    published: bool = True,
) -> PublishedResourceVersion:
    return PublishedResourceVersion(
        resource_kind=kind,
        resource_id=resource_id,
        version_id=version_id,
        state=(
            BuildState.DEGRADED if degraded else BuildState.SUCCEEDED
        ),
        published=published,
        capabilities={"keyword_search": True},
        degraded_reasons=(
            ["EMBEDDING_UNAVAILABLE"] if degraded else []
        ),
    )


def _service(
    store: _Store,
    *providers: _FakeProvider,
) -> ResourceBindingService:
    registry = ResourceVersionProviderRegistry(providers)
    return ResourceBindingService(
        uow_factory=lambda: _Uow(store),
        providers=registry,
    )


def test_published_version_accepts_protocol_constructor_shape():
    version = PublishedResourceVersion(
        "kb",
        "kb1",
        "kbv1",
        degraded=True,
        degraded_reasons=["EMBEDDING_UNAVAILABLE"],
    )

    assert version.kind == ResourceKind.KNOWLEDGE_BASE
    assert version.resource_kind == ResourceKind.KNOWLEDGE_BASE
    assert version.state == BuildState.DEGRADED
    assert version.degraded is True


@pytest.mark.asyncio
async def test_initial_binding_resolves_active_version_once_and_pins_it(
    store,
    scope,
):
    provider = _FakeProvider(
        ResourceKind.KNOWLEDGE_BASE,
        _published(ResourceKind.KNOWLEDGE_BASE, "kb1", "kbv1"),
    )
    service = _service(store, provider)

    first = await service.bind_initial(
        "s1",
        ResourceKind.KNOWLEDGE_BASE,
        "kb1",
        None,
        scope,
    )
    provider.response = _published(
        ResourceKind.KNOWLEDGE_BASE,
        "kb1",
        "kbv2",
    )
    loaded = await service.current(
        "s1",
        ResourceKind.KNOWLEDGE_BASE,
        scope,
    )

    assert first.version_id == loaded.version_id == "kbv1"
    assert first.bound_by == "u1"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_initial_retry_does_not_follow_a_new_active_version(
    store,
    scope,
):
    provider = _FakeProvider(
        ResourceKind.KNOWLEDGE_BASE,
        _published(ResourceKind.KNOWLEDGE_BASE, "kb1", "kbv1"),
    )
    service = _service(store, provider)
    first = await service.bind_initial(
        "s1",
        ResourceKind.KNOWLEDGE_BASE,
        "kb1",
        None,
        scope,
    )
    provider.response = _published(
        ResourceKind.KNOWLEDGE_BASE,
        "kb1",
        "kbv2",
    )

    retried = await service.bind_initial(
        "s1",
        ResourceKind.KNOWLEDGE_BASE,
        "kb1",
        None,
        scope,
    )

    assert retried.id == first.id
    assert retried.version_id == "kbv1"
    assert len(store.bindings) == 1


@pytest.mark.asyncio
async def test_explicit_initial_retry_rejects_a_different_version(
    store,
    scope,
):
    provider = _FakeProvider(
        ResourceKind.CODEBASE,
        _published(ResourceKind.CODEBASE, "cb1", "cbv1"),
    )
    service = _service(store, provider)
    await service.bind_initial(
        "s1",
        ResourceKind.CODEBASE,
        "cb1",
        "cbv1",
        scope,
    )
    provider.response = _published(
        ResourceKind.CODEBASE,
        "cb1",
        "cbv2",
    )

    with pytest.raises(ConflictError):
        await service.bind_initial(
            "s1",
            ResourceKind.CODEBASE,
            "cb1",
            "cbv2",
            scope,
        )

    assert len(store.bindings) == 1
    assert store.bindings[0].version_id == "cbv1"


@pytest.mark.asyncio
async def test_missing_provider_is_rejected(store, scope):
    service = _service(store)

    with pytest.raises(BadRequestError, match="provider"):
        await service.bind_initial(
            "s1",
            ResourceKind.CODEBASE,
            "cb1",
            None,
            scope,
        )


@pytest.mark.asyncio
async def test_provider_result_kind_mismatch_is_rejected(store, scope):
    provider = _FakeProvider(
        ResourceKind.CODEBASE,
        _published(ResourceKind.KNOWLEDGE_BASE, "cb1", "cbv1"),
    )
    service = _service(store, provider)

    with pytest.raises(BadRequestError, match="kind"):
        await service.bind_initial(
            "s1",
            ResourceKind.CODEBASE,
            "cb1",
            "cbv1",
            scope,
        )

    assert store.bindings == []


@pytest.mark.asyncio
async def test_provider_foreign_resource_or_version_is_rejected(
    store,
    scope,
):
    provider = _FakeProvider(
        ResourceKind.CODEBASE,
        _published(ResourceKind.CODEBASE, "other", "cbv1"),
    )
    service = _service(store, provider)

    with pytest.raises(BadRequestError, match="resource"):
        await service.bind_initial(
            "s1",
            ResourceKind.CODEBASE,
            "cb1",
            "cbv1",
            scope,
        )

    provider.response = _published(
        ResourceKind.CODEBASE,
        "cb1",
        "other-version",
    )
    with pytest.raises(BadRequestError, match="version"):
        await service.bind_initial(
            "s1",
            ResourceKind.CODEBASE,
            "cb1",
            "cbv1",
            scope,
        )


@pytest.mark.asyncio
async def test_unpublished_version_is_rejected_but_degraded_published_is_allowed(
    store,
    scope,
):
    provider = _FakeProvider(
        ResourceKind.KNOWLEDGE_BASE,
        _published(
            ResourceKind.KNOWLEDGE_BASE,
            "kb1",
            "kbv-building",
            published=False,
        ),
    )
    service = _service(store, provider)

    with pytest.raises(BadRequestError, match="published"):
        await service.bind_initial(
            "s1",
            ResourceKind.KNOWLEDGE_BASE,
            "kb1",
            "kbv-building",
            scope,
        )

    provider.response = _published(
        ResourceKind.KNOWLEDGE_BASE,
        "kb1",
        "kbv-degraded",
        degraded=True,
    )
    binding = await service.bind_initial(
        "s1",
        ResourceKind.KNOWLEDGE_BASE,
        "kb1",
        "kbv-degraded",
        scope,
    )

    assert binding.version_id == "kbv-degraded"


@pytest.mark.asyncio
async def test_cross_owner_session_is_denied_before_provider_resolution(
    store,
):
    provider = _FakeProvider(
        ResourceKind.CODEBASE,
        _published(ResourceKind.CODEBASE, "cb1", "cbv1"),
    )
    service = _service(store, provider)

    with pytest.raises(NotFoundError):
        await service.bind_initial(
            "s1",
            ResourceKind.CODEBASE,
            "cb1",
            None,
            OwnerScope.personal("intruder"),
        )

    assert provider.calls == []
    assert store.bindings == []


@pytest.mark.asyncio
async def test_initial_binding_rejects_spoofed_actor(store, scope):
    provider = _FakeProvider(
        ResourceKind.CODEBASE,
        _published(ResourceKind.CODEBASE, "cb1", "cbv1"),
    )
    service = _service(store, provider)

    with pytest.raises(ForbiddenError):
        await service.bind_initial(
            "s1",
            ResourceKind.CODEBASE,
            "cb1",
            None,
            scope,
            actor_id="other-user",
        )

    assert provider.calls == []
    assert store.bindings == []


@pytest.mark.asyncio
async def test_initial_race_creates_exactly_one_current_binding(
    store,
    scope,
):
    provider = _FakeProvider(
        ResourceKind.CODEBASE,
        _published(ResourceKind.CODEBASE, "cb1", "cbv1"),
    )
    provider.release = asyncio.Event()
    service = _service(store, provider)

    first = asyncio.create_task(
        service.bind_initial(
            "s1",
            ResourceKind.CODEBASE,
            "cb1",
            None,
            scope,
        )
    )
    second = asyncio.create_task(
        service.bind_initial(
            "s1",
            ResourceKind.CODEBASE,
            "cb1",
            None,
            scope,
        )
    )
    await asyncio.sleep(0)
    provider.release.set()
    left, right = await asyncio.gather(first, second)

    assert left.id == right.id
    assert len(store.bindings) == 1
    assert sum(binding.is_current for binding in store.bindings) == 1


@pytest.mark.asyncio
async def test_upgrade_keeps_history_and_same_target_is_idempotent(
    store,
    scope,
):
    provider = _FakeProvider(
        ResourceKind.CODEBASE,
        _published(ResourceKind.CODEBASE, "cb1", "cbv1"),
    )
    service = _service(store, provider)
    old = await service.bind_initial(
        "s1",
        ResourceKind.CODEBASE,
        "cb1",
        "cbv1",
        scope,
    )
    provider.response = _published(
        ResourceKind.CODEBASE,
        "cb1",
        "cbv2",
    )

    new = await service.upgrade(
        "s1",
        ResourceKind.CODEBASE,
        "cbv2",
        actor_id="u1",
        scope=scope,
    )
    retry = await service.upgrade(
        "s1",
        ResourceKind.CODEBASE,
        "cbv2",
        actor_id="u1",
        scope=scope,
    )
    history = await service.history(
        "s1",
        ResourceKind.CODEBASE,
        scope,
    )

    assert new.supersedes_binding_id == old.id
    assert retry.id == new.id
    assert [item.version_id for item in history] == ["cbv1", "cbv2"]
    assert [item.is_current for item in history] == [False, True]
    assert (
        await service.current_version_id(
            "s1",
            ResourceKind.CODEBASE,
            scope,
        )
        == "cbv2"
    )


@pytest.mark.asyncio
async def test_concurrent_same_target_upgrade_adds_one_history_row(
    store,
    scope,
):
    provider = _FakeProvider(
        ResourceKind.KNOWLEDGE_BASE,
        _published(ResourceKind.KNOWLEDGE_BASE, "kb1", "kbv1"),
    )
    service = _service(store, provider)
    await service.bind_initial(
        "s1",
        ResourceKind.KNOWLEDGE_BASE,
        "kb1",
        "kbv1",
        scope,
    )
    provider.response = _published(
        ResourceKind.KNOWLEDGE_BASE,
        "kb1",
        "kbv2",
        degraded=True,
    )

    left, right = await asyncio.gather(
        service.upgrade(
            "s1",
            ResourceKind.KNOWLEDGE_BASE,
            "kbv2",
            actor_id="u1",
            scope=scope,
        ),
        service.upgrade(
            "s1",
            ResourceKind.KNOWLEDGE_BASE,
            "kbv2",
            actor_id="u1",
            scope=scope,
        ),
    )

    assert left.id == right.id
    assert len(store.bindings) == 2
    assert sum(binding.is_current for binding in store.bindings) == 1


@pytest.mark.asyncio
async def test_upgrade_insert_failure_rolls_back_old_current(
    store,
    scope,
):
    provider = _FakeProvider(
        ResourceKind.CODEBASE,
        _published(ResourceKind.CODEBASE, "cb1", "cbv1"),
    )
    service = _service(store, provider)
    old = await service.bind_initial(
        "s1",
        ResourceKind.CODEBASE,
        "cb1",
        "cbv1",
        scope,
    )
    provider.response = _published(
        ResourceKind.CODEBASE,
        "cb1",
        "cbv2",
    )
    store.fail_after_deactivate = True

    with pytest.raises(RuntimeError, match="injected insert failure"):
        await service.upgrade(
            "s1",
            ResourceKind.CODEBASE,
            "cbv2",
            actor_id="u1",
            scope=scope,
        )

    assert len(store.bindings) == 1
    assert store.bindings[0].id == old.id
    assert store.bindings[0].is_current is True


@pytest.mark.asyncio
async def test_upgrade_rejects_spoofed_actor_and_missing_binding(
    store,
    scope,
):
    provider = _FakeProvider(
        ResourceKind.CODEBASE,
        _published(ResourceKind.CODEBASE, "cb1", "cbv2"),
    )
    service = _service(store, provider)

    with pytest.raises(ForbiddenError):
        await service.upgrade(
            "s1",
            ResourceKind.CODEBASE,
            "cbv2",
            actor_id="other-user",
            scope=scope,
        )
    with pytest.raises(NotFoundError, match="binding"):
        await service.upgrade(
            "s1",
            ResourceKind.CODEBASE,
            "cbv2",
            actor_id="u1",
            scope=scope,
        )


@pytest.mark.asyncio
async def test_kb_bind_initial_rechecks_under_kb_lock_after_provider_race(
    store,
    scope,
):
    provider = _FakeProvider(
        ResourceKind.KNOWLEDGE_BASE,
        _published(ResourceKind.KNOWLEDGE_BASE, "kb1", "kbv1"),
    )
    provider.after_resolve = lambda response: store.deleted_versions.add(
        response.version_id
    )
    service = _service(store, provider)

    with pytest.raises(ConflictError, match="no longer"):
        await service.bind_initial(
            "s1",
            ResourceKind.KNOWLEDGE_BASE,
            "kb1",
            "kbv1",
            scope,
        )

    assert store.kb_lock_calls == ["kb1"]
    assert store.version_rechecks == [("kb1", "kbv1")]
    assert store.bindings == []


@pytest.mark.asyncio
async def test_kb_bind_initial_resolved_rechecks_in_callers_locked_uow(
    store,
    scope,
):
    resolved = _published(
        ResourceKind.KNOWLEDGE_BASE,
        "kb1",
        "kbv1",
    )
    service = _service(
        store,
        _FakeProvider(ResourceKind.KNOWLEDGE_BASE, resolved),
    )
    store.deleted_versions.add("kbv1")
    uow = _Uow(store)

    async with uow:
        with pytest.raises(ConflictError, match="no longer"):
            await service.bind_initial_resolved(
                uow,
                session_id="s1",
                resolved=resolved,
                scope=scope,
                actor_id="u1",
            )

    assert store.kb_lock_calls == ["kb1"]
    assert store.version_rechecks == [("kb1", "kbv1")]
    assert store.bindings == []


@pytest.mark.asyncio
async def test_kb_upgrade_rechecks_target_under_same_kb_lock(
    store,
    scope,
):
    provider = _FakeProvider(
        ResourceKind.KNOWLEDGE_BASE,
        _published(ResourceKind.KNOWLEDGE_BASE, "kb1", "kbv1"),
    )
    service = _service(store, provider)
    await service.bind_initial(
        "s1",
        ResourceKind.KNOWLEDGE_BASE,
        "kb1",
        "kbv1",
        scope,
    )
    store.kb_lock_calls.clear()
    store.version_rechecks.clear()
    provider.response = _published(
        ResourceKind.KNOWLEDGE_BASE,
        "kb1",
        "kbv2",
    )
    provider.after_resolve = lambda response: store.deleted_versions.add(
        response.version_id
    )

    with pytest.raises(ConflictError, match="no longer"):
        await service.upgrade(
            "s1",
            ResourceKind.KNOWLEDGE_BASE,
            "kbv2",
            actor_id="u1",
            scope=scope,
        )

    assert store.kb_lock_calls == ["kb1"]
    assert store.version_rechecks == [("kb1", "kbv2")]
    assert len(store.bindings) == 1
    assert store.bindings[0].version_id == "kbv1"
    assert store.bindings[0].is_current is True
