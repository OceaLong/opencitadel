#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Production-service boundary proofs for atomic session resource pinning."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import count
from unittest.mock import MagicMock

import pytest

from app.application.services.codebase_service import CodebaseService
from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.application.services.resource_binding_service import ResourceBindingService
from app.application.services.resource_guard_service import ResourceGuardService
from app.application.services.session_service import SessionService
from app.domain.models.codebase import (
    Codebase,
    CodebaseSourceType,
    CodebaseStatus,
    SessionMode,
)
from app.domain.models.knowledge_base import KBStatus, KnowledgeBase
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.resource_governance import (
    BuildState,
    PublishedResourceVersion,
    ResourceKind,
    SessionResourceBinding,
)
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session
from app.domain.services.resource_version_provider import (
    ResourceVersionProviderRegistry,
)


class _Provider:
    def __init__(self, kind: ResourceKind) -> None:
        self.kind = kind

    async def resolve_published_version(
        self,
        resource_id: str,
        requested_version_id: str | None,
        _scope: OwnerScope,
    ) -> PublishedResourceVersion:
        return PublishedResourceVersion(
            resource_kind=self.kind,
            resource_id=resource_id,
            version_id=requested_version_id or f"{resource_id}-v1",
            state=BuildState.SUCCEEDED,
            published=True,
        )


@dataclass
class _Store:
    sessions: dict[str, Session] = field(default_factory=dict)
    bindings: list[SessionResourceBinding] = field(default_factory=list)
    operations: list[tuple[int, str, str]] = field(default_factory=list)
    fail_binding_kind: ResourceKind | None = None
    fail_write_commit: bool = False
    uow_ids: count = field(default_factory=lambda: count(1))
    knowledge_bases: dict[str, KnowledgeBase] = field(default_factory=lambda: {
        "kb1": KnowledgeBase(
            id="kb1",
            name="KB",
            status=KBStatus.READY,
            active_version_id="kb1-v1",
            ready_doc_count=1,
            owner_user_id="owner",
        ),
    })
    codebases: dict[str, Codebase] = field(default_factory=lambda: {
        "cb1": Codebase(
            id="cb1",
            name="Code",
            source_type=CodebaseSourceType.FILES,
            status=CodebaseStatus.READY,
            owner_user_id="owner",
        ),
    })


class _SessionRepository:
    def __init__(self, uow: "_AtomicUow") -> None:
        self._uow = uow

    async def save(self, session: Session) -> None:
        self._uow.sessions[session.id] = session
        self._uow.dirty = True
        self._uow.store.operations.append(
            (self._uow.id, "session", session.id)
        )

    async def get_metadata(self, session_id: str, scope=None):
        session = self._uow.sessions.get(session_id)
        if session is None:
            return None
        if scope is not None and session.owner_user_id != scope.user_id:
            return None
        return session

    async def lock_by_id(self, session_id: str, scope=None):
        return await self.get_metadata(session_id, scope=scope)


class _ResourceGovernanceRepository:
    def __init__(self, uow: "_AtomicUow") -> None:
        self._uow = uow

    async def get_current_binding(
        self,
        session_id: str,
        resource_kind: ResourceKind,
        *,
        for_update: bool = False,
    ):
        del for_update
        return next(
            (
                binding
                for binding in self._uow.bindings
                if binding.session_id == session_id
                and binding.resource_kind == resource_kind
                and binding.is_current
            ),
            None,
        )

    async def add_binding(
        self,
        binding: SessionResourceBinding,
    ) -> SessionResourceBinding:
        self._uow.store.operations.append(
            (self._uow.id, f"binding:{binding.resource_kind.value}", binding.id)
        )
        if self._uow.store.fail_binding_kind is binding.resource_kind:
            raise RuntimeError(f"injected {binding.resource_kind.value} binding failure")
        self._uow.bindings.append(binding)
        self._uow.dirty = True
        return binding


class _KnowledgeBaseRepository:
    def __init__(self, uow: "_AtomicUow") -> None:
        self._uow = uow

    async def get_kb(self, kb_id: str, scope=None):
        kb = self._uow.store.knowledge_bases.get(kb_id)
        if kb is None:
            return None
        if scope is not None and kb.owner_user_id != scope.user_id:
            return None
        return kb.model_copy(deep=True)

    async def get_kb_for_update(self, kb_id: str, scope=None):
        return await self.get_kb(kb_id, scope=scope)

    async def count_ready_documents(self, kb_ids: list[str]):
        return {kb_id: 1 for kb_id in kb_ids}


class _KnowledgeVersionRepository:
    async def get_version(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
    ):
        if (
            knowledge_base_id == "kb1"
            and version_id == "kb1-v1"
        ):
            return KnowledgeBaseVersion(
                id=version_id,
                knowledge_base_id=knowledge_base_id,
                state=KnowledgeVersionState.READY,
                published_at=datetime.now(timezone.utc),
            )
        return None


class _CodebaseRepository:
    def __init__(self, uow: "_AtomicUow") -> None:
        self._uow = uow

    async def get_by_id(self, codebase_id: str, scope=None):
        codebase = self._uow.store.codebases.get(codebase_id)
        if codebase is None:
            return None
        if scope is not None and codebase.owner_user_id != scope.user_id:
            return None
        return codebase.model_copy(deep=True)


class _OptionalRepository:
    async def get_by_id(self, _item_id: str, scope=None):
        del scope
        return None


class _AtomicUow:
    def __init__(self, store: _Store) -> None:
        self.store = store
        self.id = next(store.uow_ids)
        self.dirty = False
        self.sessions: dict[str, Session] = {}
        self.bindings: list[SessionResourceBinding] = []

    async def __aenter__(self):
        self.sessions = deepcopy(self.store.sessions)
        self.bindings = deepcopy(self.store.bindings)
        self.session = _SessionRepository(self)
        self.resource_governance = _ResourceGovernanceRepository(self)
        self.knowledge_base = _KnowledgeBaseRepository(self)
        self.knowledge_version = _KnowledgeVersionRepository()
        self.codebase = _CodebaseRepository(self)
        self.llm_model = _OptionalRepository()
        self.skill = _OptionalRepository()
        return self

    async def __aexit__(self, exc_type, _exc, _tb):
        if exc_type is not None:
            return False
        if self.dirty and self.store.fail_write_commit:
            raise RuntimeError("injected commit failure")
        if self.dirty:
            self.store.sessions = self.sessions
            self.store.bindings = self.bindings
        return False


def _services(store: _Store):
    factory = lambda: _AtomicUow(store)
    providers = ResourceVersionProviderRegistry([
        _Provider(ResourceKind.CODEBASE),
        _Provider(ResourceKind.KNOWLEDGE_BASE),
    ])
    guard = ResourceGuardService(providers=providers)
    binding = ResourceBindingService(
        uow_factory=factory,
        providers=providers,
    )
    return {
        "generic": SessionService(
            uow_factory=factory,
            sandbox_cls=MagicMock(),
            resource_guard=guard,
            resource_binding_service=binding,
        ),
        "knowledge_base": KnowledgeBaseService(
            uow_factory=factory,
            file_storage=MagicMock(),
            resource_guard=guard,
            resource_binding_service=binding,
        ),
        "codebase": CodebaseService(
            uow_factory=factory,
            sandbox_cls=MagicMock(),
            file_storage=MagicMock(),
            resource_guard=guard,
            resource_binding_service=binding,
        ),
    }


async def _create(factory: str, store: _Store) -> Session:
    service = _services(store)[factory]
    scope = OwnerScope.personal("owner")
    if factory == "generic":
        return await service.create_session(
            codebase_id="cb1",
            knowledge_base_id="kb1",
            mode=SessionMode.AGENT,
            scope=scope,
        )
    if factory == "knowledge_base":
        return await service.create_session_for_kb(
            "kb1",
            mode=SessionMode.AGENT,
            scope=scope,
        )
    return await service.create_session_for_codebase(
        "cb1",
        mode=SessionMode.AGENT,
        scope=scope,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factory", "expected_kinds"),
    [
        ("generic", {ResourceKind.CODEBASE, ResourceKind.KNOWLEDGE_BASE}),
        ("knowledge_base", {ResourceKind.KNOWLEDGE_BASE}),
        ("codebase", {ResourceKind.CODEBASE}),
    ],
)
async def test_successful_creation_commits_session_and_all_pins_in_one_uow(
    factory,
    expected_kinds,
):
    store = _Store()

    session = await _create(factory, store)

    committed = [
        binding for binding in store.bindings
        if binding.session_id == session.id
    ]
    assert session.id in store.sessions
    assert {binding.resource_kind for binding in committed} == expected_kinds
    operation_uows = {
        uow_id
        for uow_id, operation, _item_id in store.operations
        if operation == "session" or operation.startswith("binding:")
    }
    assert len(operation_uows) == 1


@pytest.mark.asyncio
async def test_generic_second_binding_failure_rolls_back_session_and_first_pin():
    store = _Store(fail_binding_kind=ResourceKind.KNOWLEDGE_BASE)

    with pytest.raises(RuntimeError, match="knowledge_base binding failure"):
        await _create("generic", store)

    assert store.sessions == {}
    assert store.bindings == []
    assert [operation for _, operation, _ in store.operations] == [
        "session",
        "binding:codebase",
        "binding:knowledge_base",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factory", "kind"),
    [
        ("knowledge_base", ResourceKind.KNOWLEDGE_BASE),
        ("codebase", ResourceKind.CODEBASE),
    ],
)
async def test_specialized_binding_failure_rolls_back_session(factory, kind):
    store = _Store(fail_binding_kind=kind)

    with pytest.raises(RuntimeError, match="binding failure"):
        await _create(factory, store)

    assert store.sessions == {}
    assert store.bindings == []


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", ["generic", "knowledge_base", "codebase"])
async def test_commit_failure_rolls_back_session_and_all_initial_pins(factory):
    store = _Store(fail_write_commit=True)

    with pytest.raises(RuntimeError, match="commit failure"):
        await _create(factory, store)

    assert store.sessions == {}
    assert store.bindings == []
