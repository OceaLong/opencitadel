"""Production-service boundary proofs for atomic session resource pinning."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.application.services.resource_binding_service import ResourceBindingService
from app.application.services.resource_guard_service import ResourceGuardService
from app.application.services.session_service import SessionService
from app.domain.models.knowledge_base import KBStatus, KnowledgeBase
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.resource_bindings import (
    PublicationState,
    PublishedResourceVersion,
    ResourceKind,
    SessionResourceBinding,
)
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session
from app.domain.models.session_mode import SessionMode
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
            state=PublicationState.READY,
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
    knowledge_bases: dict[str, KnowledgeBase] = field(
        default_factory=lambda: {
            "kb1": KnowledgeBase(
                id="kb1",
                name="KB",
                status=KBStatus.READY,
                active_version_id="kb1-v1",
                ready_doc_count=1,
                owner_user_id="owner",
            ),
        }
    )


class _SessionRepository:
    def __init__(self, uow: _AtomicUow) -> None:
        self._uow = uow

    async def save(self, session: Session) -> None:
        self._uow.sessions[session.id] = session
        self._uow.dirty = True
        self._uow.store.operations.append((self._uow.id, "session", session.id))

    async def get_metadata(self, session_id: str, scope=None):
        session = self._uow.sessions.get(session_id)
        if session is None:
            return None
        if scope is not None and session.owner_user_id != scope.user_id:
            return None
        return session

    async def lock_by_id(self, session_id: str, scope=None):
        return await self.get_metadata(session_id, scope=scope)


class _SessionResourceBindingRepository:
    def __init__(self, uow: _AtomicUow) -> None:
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
    def __init__(self, uow: _AtomicUow) -> None:
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
        return dict.fromkeys(kb_ids, 1)


class _KnowledgeVersionRepository:
    async def get_version(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
    ):
        if knowledge_base_id == "kb1" and version_id == "kb1-v1":
            return KnowledgeBaseVersion(
                id=version_id,
                knowledge_base_id=knowledge_base_id,
                state=KnowledgeVersionState.READY,
                published_at=datetime.now(UTC),
            )
        return None


class _OptionalRepository:
    async def get_by_id(self, _item_id: str, scope=None):
        del scope


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
        self.resource_bindings = _SessionResourceBindingRepository(self)
        self.knowledge_base = _KnowledgeBaseRepository(self)
        self.knowledge_version = _KnowledgeVersionRepository()
        self.inference_model = _OptionalRepository()
        self.skill = _OptionalRepository()
        return self

    async def commit(self):
        if self.dirty and self.store.fail_write_commit:
            raise RuntimeError("injected commit failure")
        if self.dirty:
            self.store.sessions = self.sessions
            self.store.bindings = self.bindings

    async def __aexit__(self, exc_type, _exc, _tb):
        return False


def _services(store: _Store):
    def factory():
        return _AtomicUow(store)

    providers = ResourceVersionProviderRegistry([_Provider(ResourceKind.KNOWLEDGE_BASE)])
    guard = ResourceGuardService(providers=providers)
    binding = ResourceBindingService(
        uow_factory=factory,
        providers=providers,
    )
    return {
        "generic": SessionService(
            uow_factory=factory,
            sandbox_factory=MagicMock(),
            run_projection=AsyncMock(),
            session_list_publisher=AsyncMock(),
            resource_guard=guard,
            resource_binding_service=binding,
        ),
        "knowledge_base": KnowledgeBaseService(
            uow_factory=factory,
            file_storage=MagicMock(),
            run_admission_service=AsyncMock(),
            run_control_service=AsyncMock(),
            run_projection=AsyncMock(),
            web_documents=AsyncMock(),
            resource_guard=guard,
            resource_binding_service=binding,
        ),
    }


async def _create(factory: str, store: _Store) -> Session:
    service = _services(store)[factory]
    scope = OwnerScope.personal("owner")
    if factory == "generic":
        return await service.create_session(
            knowledge_base_id="kb1",
            mode=SessionMode.AGENT,
            scope=scope,
        )
    return await service.create_session_for_kb(
        "kb1",
        mode=SessionMode.AGENT,
        scope=scope,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factory", "expected_kinds"),
    [
        ("generic", {ResourceKind.KNOWLEDGE_BASE}),
        ("knowledge_base", {ResourceKind.KNOWLEDGE_BASE}),
    ],
)
async def test_successful_creation_commits_session_and_all_pins_in_one_uow(
    factory,
    expected_kinds,
):
    store = _Store()

    session = await _create(factory, store)

    committed = [binding for binding in store.bindings if binding.session_id == session.id]
    assert session.id in store.sessions
    assert {binding.resource_kind for binding in committed} == expected_kinds
    operation_uows = {
        uow_id
        for uow_id, operation, _item_id in store.operations
        if operation == "session" or operation.startswith("binding:")
    }
    assert len(operation_uows) == 1


@pytest.mark.asyncio
async def test_generic_binding_failure_rolls_back_session():
    store = _Store(fail_binding_kind=ResourceKind.KNOWLEDGE_BASE)

    with pytest.raises(RuntimeError, match="knowledge_base binding failure"):
        await _create("generic", store)

    assert store.sessions == {}
    assert store.bindings == []
    assert [operation for _, operation, _ in store.operations] == [
        "session",
        "binding:knowledge_base",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factory", "kind"),
    [
        ("knowledge_base", ResourceKind.KNOWLEDGE_BASE),
    ],
)
async def test_specialized_binding_failure_rolls_back_session(factory, kind):
    store = _Store(fail_binding_kind=kind)

    with pytest.raises(RuntimeError, match="binding failure"):
        await _create(factory, store)

    assert store.sessions == {}
    assert store.bindings == []


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", ["generic", "knowledge_base"])
async def test_commit_failure_rolls_back_session_and_all_initial_pins(factory):
    store = _Store(fail_write_commit=True)

    with pytest.raises(RuntimeError, match="commit failure"):
        await _create(factory, store)

    assert store.sessions == {}
    assert store.bindings == []
