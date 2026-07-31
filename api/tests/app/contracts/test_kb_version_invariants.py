#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Executable cross-layer acceptance invariants for versioned KB ingestion."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.application.services.knowledge_base_service import (
    KnowledgeBaseService,
)
from app.application.services.knowledge_version_service import (
    KnowledgeVersionService,
)
from app.application.services.resource_guard_service import (
    ResourceGuardService,
)
from app.domain.models.codebase import SessionMode
from app.domain.models.knowledge_base import (
    ChunkLevel,
    KBStatus,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
)
from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceKind,
    SessionResourceBinding,
)
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session
from app.domain.repositories.knowledge_base_repository import (
    DocumentPage,
    VersionedKnowledgeChunk,
)
from app.domain.services.knowledge_base.ingestion_runner import (
    KBIngestionRunner,
)
from app.domain.services.knowledge_base.rerank_service import RerankSettings
from app.domain.services.knowledge_base.retriever import HybridRetriever
from app.domain.services.knowledge_base.version_builder import (
    KnowledgeVersionBuilder,
)
from app.domain.services.resource_version_provider import (
    ResourceVersionProviderRegistry,
)
from app.infrastructure.models.knowledge_base import (
    KnowledgeBaseModel,
    KnowledgeChunkModel,
    KnowledgeDocumentModel,
    KnowledgeEntityModel,
    KnowledgeEntityRefModel,
    KnowledgeRelationModel,
)
from app.infrastructure.models.knowledge_version import (
    KnowledgeDocumentRevisionORM,
    KnowledgeVersionDocumentORM,
)
from app.infrastructure.repositories.db_knowledge_base_repository import (
    DBKnowledgeBaseRepository,
)
from tests.app.application.services.test_session_creation_atomicity import (
    _Store as _AtomicStore,
    _services as _atomic_services,
)
from tests.app.application.services.test_knowledge_base_build_commands import (
    _VersionSurfaceUow,
)
from tests.app.application.services.test_task_runner_factory import (
    _build_factory,
)
from tests.app.application.services.test_task_runner_factory_kb_binding import (
    _Uow as _RunnerAuthorizationUow,
    _binding as _runner_binding,
    _version as _runner_version,
)
from tests.app.domain.services.knowledge_base.test_version_builder import (
    _State as _BuilderState,
    _Uow as _BuilderUow,
    _failed_candidate,
)
from tests.app.domain.services.knowledge_base.test_versioned_ingestion_runner import (
    _RunnerBuildService,
    _RunnerStore,
    _RunnerUow,
    _run_candidate,
)
from tests.app.infrastructure.repositories.test_db_knowledge_version_gc_repository import (
    NOW,
    _seed_binding,
    _seed_kb,
    _seed_version,
)


pytest_plugins = (
    "tests.app.infrastructure.repositories."
    "test_db_knowledge_version_gc_repository",
)

_SCOPE = OwnerScope.personal("owner")
_TERMINAL_BUILD_STATES = {
    BuildState.SUCCEEDED,
    BuildState.DEGRADED,
    BuildState.FAILED,
    BuildState.CANCELLED,
}


class _HarnessUow:
    def __init__(self, knowledge, version) -> None:
        self.knowledge_base = knowledge
        self.knowledge_version = version

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False


class _StoreVersionRepository:
    def __init__(self, store: _RunnerStore) -> None:
        self.store = store

    async def get_version(self, version_id, *, knowledge_base_id):
        if knowledge_base_id != self.store.kb.id:
            return None
        if version_id == self.store.parent_version.id:
            return self.store.parent_version
        if version_id == self.store.version.id:
            return self.store.version
        return None


class _PublishedClosureRepository:
    """Version-scoped searchable/source-readable view over the runner store."""

    def __init__(self, store: _RunnerStore) -> None:
        self.store = store
        self.graph_reads = 0

    def _published(self, version_id: str) -> KnowledgeBaseVersion | None:
        if version_id == self.store.parent_version.id:
            return self.store.parent_version
        if version_id == self.store.version.id:
            version = self.store.version
            if (
                version.published_at is not None
                and version.state
                in {
                    KnowledgeVersionState.READY,
                    KnowledgeVersionState.DEGRADED,
                }
            ):
                return version
        return None

    def _identity(self, version_id: str) -> tuple[str, str]:
        if version_id == self.store.parent_version.id:
            return "doc-0", "rev-v1"
        entry = self.store.manifest[0]
        return entry.document_id, entry.document_revision_id

    def _chunks(self, version_id: str) -> tuple[KnowledgeChunk, KnowledgeChunk]:
        doc_id, _revision_id = self._identity(version_id)
        if version_id == self.store.parent_version.id:
            parent = KnowledgeChunk(
                id="v1-doc-0-p",
                kb_id=self.store.kb.id,
                doc_id=doc_id,
                version_id=version_id,
                level=ChunkLevel.PARENT,
                content="known result from published v1",
                page_no=1,
                ordinal=0,
            )
            child = KnowledgeChunk(
                id="v1-doc-0-c",
                kb_id=self.store.kb.id,
                doc_id=doc_id,
                version_id=version_id,
                parent_id=parent.id,
                level=ChunkLevel.CHILD,
                content="known result",
                page_no=1,
                ordinal=1,
            )
            return parent, child
        parent = next(
            chunk
            for chunk in self.store.chunks
            if chunk.level is ChunkLevel.PARENT
        )
        child = next(
            chunk
            for chunk in self.store.chunks
            if chunk.level is ChunkLevel.CHILD
        )
        return parent, child

    async def get_kb(self, kb_id, scope=None):
        del scope
        return self.store.kb if kb_id == self.store.kb.id else None

    async def count_ready_documents(self, kb_ids):
        return {kb_id: 1 for kb_id in kb_ids}

    async def bm25_search_chunks_for_version(
        self,
        kb_id,
        version_id,
        segmented_query,
        *,
        limit,
    ):
        del limit
        if (
            kb_id != self.store.kb.id
            or self._published(version_id) is None
            or "known" not in segmented_query
        ):
            return []
        _parent, child = self._chunks(version_id)
        doc_id, revision_id = self._identity(version_id)
        return [
            VersionedKnowledgeChunk(
                chunk=child,
                document=self.store.documents[doc_id],
                document_revision_id=revision_id,
                score=1.0,
            )
        ]

    async def vector_search_chunks_for_version(self, *_args, **_kwargs):
        return []

    async def get_related_chunk_ids_for_version(self, *_args, **_kwargs):
        return []

    async def get_chunks_by_ids_for_version(self, *_args, **_kwargs):
        return []

    async def get_parents_by_ids_for_version(
        self,
        kb_id,
        version_id,
        parent_ids,
    ):
        if (
            kb_id != self.store.kb.id
            or self._published(version_id) is None
        ):
            return []
        parent, _child = self._chunks(version_id)
        return [parent] if parent.id in parent_ids else []

    async def get_document_for_version(self, kb_id, version_id, doc_id):
        if (
            kb_id != self.store.kb.id
            or self._published(version_id) is None
        ):
            return None
        expected_doc_id, revision_id = self._identity(version_id)
        if doc_id != expected_doc_id:
            return None
        return self.store.documents[doc_id], revision_id

    async def read_document_page_for_version(
        self,
        kb_id,
        version_id,
        doc_id,
        revision_id,
        *,
        page_no=None,
        cursor=None,
        limit=30,
    ):
        del cursor, limit
        resolved = await self.get_document_for_version(
            kb_id,
            version_id,
            doc_id,
        )
        if resolved is None or resolved[1] != revision_id:
            return DocumentPage(
                items=(),
                next_cursor=None,
                total=0,
                truncated=False,
            )
        parent, _child = self._chunks(version_id)
        if page_no is not None and parent.page_no != page_no:
            return DocumentPage(
                items=(),
                next_cursor=None,
                total=0,
                truncated=False,
            )
        return DocumentPage(
            items=(parent,),
            next_cursor=None,
            total=1,
            truncated=False,
        )

    async def list_entities_page_for_version(self, *_args, **_kwargs):
        self.graph_reads += 1
        raise AssertionError("disabled graph must not expose partial rows")

    async def list_relations_for_entities_for_version(
        self,
        *_args,
        **_kwargs,
    ):
        self.graph_reads += 1
        raise AssertionError("disabled graph must not expose partial rows")

    async def get_entities_by_ids_for_version(self, *_args, **_kwargs):
        self.graph_reads += 1
        raise AssertionError("disabled graph must not expose partial rows")


def _store_uow(store: _RunnerStore):
    knowledge = _PublishedClosureRepository(store)
    version = _StoreVersionRepository(store)
    return knowledge, lambda: _HarnessUow(knowledge, version)


async def _query_and_source_snapshot(
    store: _RunnerStore,
    version_id: str,
) -> tuple[object, ...]:
    knowledge, uow_factory = _store_uow(store)
    vector = SimpleNamespace(
        embed=AsyncMock(return_value=[0.1] * 1536),
    )
    response = await HybridRetriever(
        uow_factory=uow_factory,
        vector_service=vector,
        rerank_settings=RerankSettings(enabled=False),
    ).retrieve(store.kb.id, version_id, "known", limit=5)
    doc_id, _revision_id = knowledge._identity(version_id)
    document, revision_id, page = await KnowledgeBaseService(
        uow_factory=uow_factory,
        file_storage=SimpleNamespace(),
    ).read_document_page(
        store.kb.id,
        version_id,
        doc_id,
        limit=10,
        scope=_SCOPE,
    )
    return (
        tuple(item.content for item in response.items),
        tuple(
            item.citation.model_dump(mode="json")
            for item in response.items
        ),
        document.id,
        revision_id,
        tuple(item.content for item in page.items),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase",
    (
        "parse",
        "chunk",
        "keyword_index",
        "validate",
        "publish",
        "publish_commit",
    ),
)
async def test_mandatory_failure_preserves_active_query_source_and_identity(
    monkeypatch,
    phase,
):
    store = _RunnerStore()
    before = await _query_and_source_snapshot(store, "v1")
    store.fail_phase = phase

    _runner, events = await _run_candidate(monkeypatch, store)

    after = await _query_and_source_snapshot(store, "v1")
    assert after == before
    assert before[1] == ({
        "version_id": "v1",
        "document_revision_id": "rev-v1",
        "doc_id": "doc-0",
        "page_no": 1,
        "chunk_id": "v1-doc-0-p",
    },)
    assert events[-1].type == "error"
    assert store.kb.active_version_id == "v1"
    assert store.version.state is KnowledgeVersionState.FAILED
    assert store.build.state is BuildState.FAILED
    assert sum(
        event.state in _TERMINAL_BUILD_STATES
        for event in store.events
    ) == 1
    assert store.forbidden_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "capability", "reason"),
    (
        ("vector", "vector_search", "EMBEDDING_UNAVAILABLE"),
        ("graph", "graph_search", "GRAPH_UNAVAILABLE"),
    ),
)
async def test_optional_failure_publishes_queryable_truthful_degraded_version(
    monkeypatch,
    phase,
    capability,
    reason,
):
    store = _RunnerStore()
    store.fail_phase = phase

    _runner, events = await _run_candidate(monkeypatch, store)

    snapshot = await _query_and_source_snapshot(store, "v2")
    assert snapshot[0]
    assert snapshot[4]
    assert snapshot[1][0]["version_id"] == "v2"
    assert events[-1].type == "done"
    assert store.kb.active_version_id == "v2"
    assert store.version.state is KnowledgeVersionState.DEGRADED
    assert store.version.capabilities["keyword_search"] is True
    assert store.version.capabilities[capability] is False
    assert reason in store.version.degraded_reasons

    knowledge, uow_factory = _store_uow(store)
    graph = await KnowledgeBaseService(
        uow_factory=uow_factory,
        file_storage=SimpleNamespace(),
    ).get_version_graph(
        "kb-1",
        "v2",
        q=None,
        cursor=None,
        limit=10,
        scope=_SCOPE,
    )
    if not store.version.capabilities.get("graph_search", False):
        assert graph.capability is False
        assert graph.nodes == ()
        assert graph.edges == ()
        assert knowledge.graph_reads == 0


@pytest.mark.asyncio
async def test_parsed_unindexed_candidate_is_not_ask_agent_or_runner_ready(
    monkeypatch,
):
    store = _RunnerStore()
    store.fail_phase = "cancel_after_parse"
    with pytest.raises(asyncio.CancelledError):
        await _run_candidate(monkeypatch, store)

    assert store.kb.active_version_id == "v1"
    assert store.revisions["rev-0"].state is DocumentRevisionState.PARSED
    assert store.version.published_at is None
    assert store.build.state is BuildState.CANCELLED

    knowledge, uow_factory = _store_uow(store)
    with pytest.raises(ValueError, match="not published"):
        await HybridRetriever(
            uow_factory=uow_factory,
            rerank_settings=RerankSettings(enabled=False),
        ).retrieve("kb-1", "v2", "known", limit=5)

    provider = KnowledgeVersionService(uow_factory=uow_factory)
    guard = ResourceGuardService(
        providers=ResourceVersionProviderRegistry([provider])
    )
    service = KnowledgeBaseService(
        uow_factory=uow_factory,
        file_storage=SimpleNamespace(),
        resource_guard=guard,
    )
    for mode in (SessionMode.ASK, SessionMode.AGENT):
        with pytest.raises(BadRequestError, match="not published"):
            await service.create_session_for_kb(
                "kb-1",
                mode=mode,
                knowledge_base_version_id="v2",
                scope=_SCOPE,
            )
    assert await knowledge.count_ready_documents(["kb-1"]) == {"kb-1": 1}

    runner_factory = _build_factory(AsyncMock())
    runner_factory._uow_factory = lambda: _RunnerAuthorizationUow(
        _runner_version(
            version_id="kbv2",
            state=KnowledgeVersionState.BUILDING,
            published=False,
        )
    )
    session = Session(
        id="session-building",
        knowledge_base_id="kb1",
        resource_bindings=[_runner_binding("kbv2")],
        owner_user_id="user1",
    )
    with pytest.raises(NotFoundError, match="已发布"):
        await runner_factory._authorize_session_resources(
            session,
            runner_factory._scope_for_session(session),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", (SessionMode.ASK, SessionMode.AGENT))
async def test_ask_and_agent_persist_one_concrete_published_version(mode):
    store = _AtomicStore()
    service = _atomic_services(store)["knowledge_base"]

    session = await service.create_session_for_kb(
        "kb1",
        mode=mode,
        scope=OwnerScope.personal("owner"),
    )

    pins = [
        binding
        for binding in store.bindings
        if binding.session_id == session.id
        and binding.resource_kind is ResourceKind.KNOWLEDGE_BASE
    ]
    assert session.mode is mode
    assert len(pins) == 1
    assert pins[0].version_id == "kb1-v1"
    assert session.resource_bindings[0].version_id == pins[0].version_id

    runner_factory = _build_factory(AsyncMock())
    runner_factory._uow_factory = lambda: _RunnerAuthorizationUow(
        _runner_version(
            version_id=pins[0].version_id,
            kb_id="kb1",
        )
    )
    _codebase, _codebase_version_id, knowledge_base, version_id = (
        await runner_factory._authorize_session_resources(
            session,
            runner_factory._scope_for_session(session),
        )
    )
    assert knowledge_base.id == pins[0].resource_id
    assert version_id == pins[0].version_id


class _PersistedKeywordRepository:
    """SQLite keyword adapter over the real persisted version closure."""

    def __init__(self, base, session) -> None:
        self.base = base
        self.session = session

    def __getattr__(self, name):
        return getattr(self.base, name)

    async def bm25_search_chunks_for_version(
        self,
        kb_id,
        version_id,
        segmented_query,
        *,
        limit,
    ):
        del segmented_query
        rows = self.session.scalars(
            select(KnowledgeChunkModel)
            .where(
                KnowledgeChunkModel.kb_id == kb_id,
                KnowledgeChunkModel.version_id == version_id,
                KnowledgeChunkModel.level == ChunkLevel.CHILD.value,
            )
            .order_by(KnowledgeChunkModel.id)
            .limit(limit)
        ).all()
        out = []
        for row in rows:
            document = self.session.get(KnowledgeDocumentModel, row.doc_id)
            manifest = self.session.get(
                KnowledgeVersionDocumentORM,
                (version_id, row.doc_id),
            )
            if document is not None and manifest is not None:
                out.append(
                    VersionedKnowledgeChunk(
                        chunk=row.to_domain(),
                        document=document.to_domain(),
                        document_revision_id=(
                            manifest.document_revision_id
                        ),
                        score=1.0,
                    )
                )
        return out

    async def vector_search_chunks_for_version(self, *_args, **_kwargs):
        return []

    async def get_related_chunk_ids_for_version(self, *_args, **_kwargs):
        return []

    async def get_chunks_by_ids_for_version(self, *_args, **_kwargs):
        return []


async def _persisted_query(gc_db, version_id: str):
    base = DBKnowledgeBaseRepository(gc_db.adapter)
    knowledge = _PersistedKeywordRepository(base, gc_db.session)
    uow_factory = lambda: _HarnessUow(knowledge, gc_db.repo)
    return await HybridRetriever(
        uow_factory=uow_factory,
        rerank_settings=RerankSettings(enabled=False),
    ).retrieve("kb-1", version_id, "retained", limit=10)


@pytest.mark.asyncio
async def test_historical_binding_survives_removal_gc_query_and_cursor_source(
    gc_db,
):
    _seed_kb(gc_db, "kb-1")
    _seed_version(gc_db, "v1", age_days=100)
    _seed_version(gc_db, "v2", age_days=1)
    gc_db.session.flush()
    gc_db.session.get(
        KnowledgeBaseModel,
        "kb-1",
    ).active_version_id = "v2"
    gc_db.session.add(
        KnowledgeDocumentModel(
            id="doc-a",
            kb_id="kb-1",
            title="Historical A",
            source_ref="source://a",
        )
    )
    gc_db.session.add(
        KnowledgeDocumentRevisionORM(
            id="rev-a",
            document_id="doc-a",
            source_ref="source://a",
            source_digest="a" * 64,
            state=DocumentRevisionState.INDEXED.value,
        )
    )
    gc_db.session.add(
        KnowledgeVersionDocumentORM(
            version_id="v1",
            knowledge_base_id="kb-1",
            document_id="doc-a",
            document_revision_id="rev-a",
            ordinal=0,
            state=DocumentRevisionState.INDEXED.value,
        )
    )
    gc_db.session.flush()
    for index, content in enumerate(("retained page one", "page two")):
        gc_db.session.add(
            KnowledgeChunkModel(
                id=f"parent-{index}",
                kb_id="kb-1",
                doc_id="doc-a",
                version_id="v1",
                level=ChunkLevel.PARENT.value,
                content=content,
                page_no=index + 1,
                ordinal=index,
            )
        )
    gc_db.session.add(
        KnowledgeChunkModel(
            id="child-a",
            kb_id="kb-1",
            doc_id="doc-a",
            version_id="v1",
            parent_id="parent-0",
            level=ChunkLevel.CHILD.value,
            content="retained keyword",
            page_no=1,
            ordinal=2,
        )
    )
    gc_db.session.add(
        KnowledgeEntityModel(
            id="entity-a",
            kb_id="kb-1",
            version_id="v1",
            name="Historical",
            normalized_name="historical",
        )
    )
    gc_db.session.flush()
    gc_db.session.add(
        KnowledgeRelationModel(
            id="relation-a",
            kb_id="kb-1",
            version_id="v1",
            src_entity_id="entity-a",
            dst_entity_id="entity-a",
            relation="supports",
            chunk_id="child-a",
        )
    )
    gc_db.session.add(
        KnowledgeEntityRefModel(
            id="ref-a",
            kb_id="kb-1",
            version_id="v1",
            entity_id="entity-a",
            doc_id="doc-a",
        )
    )
    _seed_binding(
        gc_db,
        binding_id="binding-old",
        session_id="session-old",
        kb_id="kb-1",
        version_id="v1",
        is_current=False,
    )
    gc_db.session.commit()

    binding = SessionResourceBinding(
        id="binding-old",
        session_id="session-old",
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id="kb-1",
        version_id="v1",
        is_current=False,
        bound_by="owner",
        created_at=NOW,
    )
    assert binding.to_projection().version_id == "v1"

    base = DBKnowledgeBaseRepository(gc_db.adapter)
    assert await base.get_document_for_version(
        "kb-1", "v2", "doc-a"
    ) is None
    assert await base.get_document_for_version(
        "kb-1", binding.version_id, "doc-a"
    ) is not None
    before = await _persisted_query(gc_db, binding.version_id)
    assert before.items[0].citation.model_dump(mode="json") == {
        "version_id": "v1",
        "document_revision_id": "rev-a",
        "doc_id": "doc-a",
        "page_no": 1,
        "chunk_id": "parent-0",
    }

    service = KnowledgeBaseService(
        uow_factory=lambda: _HarnessUow(base, gc_db.repo),
        file_storage=SimpleNamespace(),
    )
    _doc, revision_id, first = await service.read_document_page(
        "kb-1",
        binding.version_id,
        "doc-a",
        cursor=None,
        limit=1,
    )
    _doc, _revision_id, final = await service.read_document_page(
        "kb-1",
        binding.version_id,
        "doc-a",
        cursor=first.next_cursor,
        limit=1,
    )
    assert revision_id == "rev-a"
    assert [item.id for item in (*first.items, *final.items)] == [
        "parent-0",
        "parent-1",
    ]
    assert final.next_cursor is None

    result = await gc_db.repo.collect_garbage(
        retain_count=0,
        older_than=NOW,
        batch_size=50,
    )
    gc_db.session.commit()

    assert result.collected_version_ids == ()
    assert result.protected_bound_versions >= 1
    after = await _persisted_query(gc_db, binding.version_id)
    assert after.items[0].citation == before.items[0].citation
    assert gc_db.session.get(KnowledgeDocumentRevisionORM, "rev-a")
    assert gc_db.session.get(KnowledgeChunkModel, "child-a")
    assert gc_db.session.get(KnowledgeEntityModel, "entity-a")
    assert gc_db.session.get(KnowledgeRelationModel, "relation-a")
    assert gc_db.session.get(KnowledgeEntityRefModel, "ref-a")
    assert gc_db.session.get(KnowledgeDocumentModel, "doc-a")


@pytest.mark.asyncio
async def test_graph_api_returns_real_endpoints_evidence_and_no_placeholder():
    from tests.app.interfaces.endpoints.test_knowledge_graph_routes import (
        _KbRepo,
        _VersionRepo,
        _service,
    )

    response = await _service(_KbRepo(), _VersionRepo()).get_version_graph(
        "kb1",
        "v1",
        q="Open",
        cursor=None,
        limit=10,
        scope=OwnerScope.personal("u1"),
    )

    node_ids = {node.id for node in response.nodes}
    assert node_ids == {"e1", "e2"}
    assert all(
        edge.source in node_ids and edge.target in node_ids
        for edge in response.edges
    )
    assert response.edges[0].evidence[0].model_dump(mode="json") == {
        "version_id": "v1",
        "document_revision_id": "rev1",
        "doc_id": "doc1",
        "page_no": 4,
        "chunk_id": "chunk1",
    }
    assert all(not node.id.startswith("doc") for node in response.nodes)

    disabled = await _service(
        _KbRepo(capability=False),
        _VersionRepo(capability=False),
    ).get_version_graph(
        "kb1",
        "v1",
        q=None,
        cursor=None,
        limit=10,
        scope=OwnerScope.personal("u1"),
    )
    assert disabled.capability is False
    assert disabled.nodes == ()
    assert disabled.edges == ()


@pytest.mark.asyncio
async def test_retry_cancel_and_reconcile_preserve_active_version():
    builder_state = _BuilderState()
    failed, _version, original_manifest = _failed_candidate(builder_state)
    retried = await KnowledgeVersionBuilder(
        lambda: _BuilderUow(builder_state)
    ).retry_candidate(
        "kb1",
        failed.id,
        actor_id="user1",
        scope=OwnerScope.personal("user1"),
    )
    assert builder_state.kbs["kb1"].active_version_id == "v1"
    assert retried.build.state is BuildState.QUEUED
    assert [
        item.model_dump(exclude={"version_id"})
        for item in builder_state.manifests[retried.version.id]
    ] == [
        item.model_dump(exclude={"version_id"})
        for item in original_manifest
    ]

    knowledge_base = KnowledgeBase(
        id="kb1",
        status=KBStatus.READY,
        owner_user_id="user1",
        active_version_id="v1",
    )
    candidate = KnowledgeBaseVersion(
        id="candidate",
        knowledge_base_id="kb1",
        parent_version_id="v1",
        build_id="build-active",
    )
    build = ResourceBuild(
        id="build-active",
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id="kb1",
        version_id=candidate.id,
        parent_version_id="v1",
        command_key="command",
        state=BuildState.QUEUED,
        created_by="user1",
    )
    cancel_uow = _VersionSurfaceUow(
        knowledge_base,
        [candidate],
        {build.id: build},
    )
    task_state = SimpleNamespace(request_cancel=AsyncMock())
    cancelled = await KnowledgeBaseService(
        uow_factory=lambda: cancel_uow,
        file_storage=SimpleNamespace(),
        task_state_port=task_state,
    ).cancel_build(
        "kb1",
        build.id,
        scope=OwnerScope.personal("user1"),
    )
    assert cancelled.state is BuildState.QUEUED
    assert cancelled.can_cancel is True
    task_state.request_cancel.assert_awaited_once_with(build.id)
    assert knowledge_base.active_version_id == "v1"

    store = _RunnerStore()
    runner = KBIngestionRunner(
        uow_factory=lambda: _RunnerUow(store),
        file_storage=MagicMock(),
        build_service=_RunnerBuildService(store),
    )
    reconciled = await runner.reconcile_stale(store.build.id)
    assert reconciled is BuildState.FAILED
    assert store.kb.active_version_id == "v1"
    assert store.version.state is KnowledgeVersionState.FAILED
    assert store.build.state is BuildState.FAILED


def test_alembic_has_only_e9_head_and_d8_is_its_parent():
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert script.get_heads() == ["e9f0a1b2c3d4"]
    assert script.get_revision("e9f0a1b2c3d4").down_revision == (
        "d8e9f0a1b2c3"
    )
