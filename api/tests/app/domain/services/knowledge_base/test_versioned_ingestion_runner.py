#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Contracts for staged, version-scoped knowledge-base ingestion."""
from __future__ import annotations

import inspect
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domain.models.app_config import AppConfig
from app.domain.models.knowledge_base import ChunkLevel
from app.domain.models.knowledge_base import (
    KBSourceType,
    KBStatus,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
)
from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeBaseVersion,
    KnowledgeDocumentRevision,
    KnowledgeVersionDocument,
    KnowledgeVersionState,
)
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
)
from app.domain.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from app.domain.repositories.knowledge_version_repository import (
    KnowledgeVersionRepository,
)
from app.domain.services.knowledge_base.chunker import KBChunker
from app.domain.services.knowledge_base.ingestion_runner import (
    KBIngestionRunner,
)
from app.domain.services.knowledge_base.graph_builder import GraphBuildResult
from app.domain.services.knowledge_base.parsers import PageBlock


_TEST_UNSET = object()


def _is_patch_omitted(value) -> bool:
    return value is _TEST_UNSET or type(value).__name__ == "UnsetType"


def test_runner_entry_is_durable_build_id_not_knowledge_base_id():
    parameters = inspect.signature(KBIngestionRunner.run).parameters

    assert "build_id" in parameters
    assert "kb_id" not in parameters


@pytest.mark.asyncio
async def test_chunker_stamps_every_row_with_explicit_candidate_version():
    chunker = KBChunker()
    chunker._vector.enabled = False

    parents, children = await chunker.build_chunks(
        "kb-1",
        "doc-1",
        [PageBlock(page_no=1, heading_path="doc", text="candidate text")],
        version_id="kb-1-v2",
    )

    assert parents
    assert children
    assert {chunk.version_id for chunk in [*parents, *children]} == {
        "kb-1-v2"
    }
    assert {chunk.level for chunk in parents} == {ChunkLevel.PARENT}
    assert {chunk.level for chunk in children} == {ChunkLevel.CHILD}


def test_repository_contracts_are_candidate_scoped_and_durable():
    for method in (
        "replace_candidate_chunks",
        "replace_candidate_graph",
        "get_candidate_index_metrics",
    ):
        assert callable(getattr(KnowledgeBaseRepository, method, None))
    for method in (
        "transition_document",
        "get_build_candidate",
    ):
        assert callable(getattr(KnowledgeVersionRepository, method, None))


class _RunnerStore:
    def __init__(self, *, documents: int = 1) -> None:
        self.kb = KnowledgeBase(
            id="kb-1",
            name="KB",
            status=KBStatus.PENDING,
            active_version_id="v1",
            ingest_task_id="build-1",
            owner_user_id="owner",
        )
        self.build = ResourceBuild(
            id="build-1",
            resource_kind=ResourceKind.KNOWLEDGE_BASE,
            resource_id="kb-1",
            version_id="v2",
            parent_version_id="v1",
            command_key="candidate",
            created_by="owner",
        )
        self.version = KnowledgeBaseVersion(
            id="v2",
            knowledge_base_id="kb-1",
            parent_version_id="v1",
            build_id="build-1",
        )
        self.parent_version = KnowledgeBaseVersion(
            id="v1",
            knowledge_base_id="kb-1",
            state=KnowledgeVersionState.READY,
            capabilities={"keyword_search": True},
            metrics={"child_chunk_count": 1},
            published_at=datetime.now(timezone.utc),
        )
        self.documents = {
            f"doc-{idx}": KnowledgeDocument(
                id=f"doc-{idx}",
                kb_id="kb-1",
                title=f"doc-{idx}.txt",
                source_type=KBSourceType.UPLOAD,
                source_ref=f'{{"file_id":"file-{idx}"}}',
                file_id=f"file-{idx}",
            )
            for idx in range(documents)
        }
        self.revisions = {
            f"rev-{idx}": KnowledgeDocumentRevision(
                id=f"rev-{idx}",
                document_id=f"doc-{idx}",
                source_ref=f'{{"file_id":"file-{idx}"}}',
                source_digest=f"{idx + 1:064x}",
            )
            for idx in range(documents)
        }
        self.manifest = [
            KnowledgeVersionDocument(
                version_id="v2",
                document_id=f"doc-{idx}",
                document_revision_id=f"rev-{idx}",
                ordinal=idx,
            )
            for idx in range(documents)
        ]
        self.chunks: list[KnowledgeChunk] = []
        self.source_chunks: list[KnowledgeChunk] = []
        self.graph = ([], [], [])
        self.events: list[ResourceBuildEvent] = []
        self.fail_phase: str | None = None
        self.missing_candidate = False
        self.commit_injected = False
        self.forbidden_calls: list[str] = []
        self.parse_warning: str | None = None


class _RunnerVersionRepo:
    def __init__(self, store: _RunnerStore) -> None:
        self.store = store

    async def get_build_candidate(self, build_id):
        if build_id != self.store.build.id:
            return None
        if self.store.missing_candidate:
            return None
        return self.store.version, list(self.store.manifest)

    async def get_version(self, version_id, *, knowledge_base_id):
        assert knowledge_base_id == "kb-1"
        if self.store.missing_candidate and version_id == self.store.version.id:
            return None
        if version_id == self.store.version.id:
            return self.store.version
        if version_id == self.store.parent_version.id:
            return self.store.parent_version
        return None

    async def get_revisions(self, revision_ids, *, knowledge_base_id):
        assert knowledge_base_id == "kb-1"
        return {
            item: self.store.revisions[item]
            for item in revision_ids
        }

    async def transition_document(
        self,
        version_id,
        document_id,
        *,
        knowledge_base_id,
        state,
        parsed_blocks=_TEST_UNSET,
        page_count=_TEST_UNSET,
        error=_TEST_UNSET,
        warning=_TEST_UNSET,
    ):
        assert (version_id, knowledge_base_id) == (
            self.store.version.id,
            "kb-1",
        )
        index = next(
            idx
            for idx, item in enumerate(self.store.manifest)
            if item.document_id == document_id
        )
        entry = self.store.manifest[index]
        revision = self.store.revisions[entry.document_revision_id]
        manifest_update = {"state": state}
        if not _is_patch_omitted(error):
            manifest_update["error"] = error
        if not _is_patch_omitted(warning):
            manifest_update["warning"] = warning
        self.store.manifest[index] = entry.model_copy(update=manifest_update)
        if revision.state is not DocumentRevisionState.INDEXED:
            revision_update = {"state": state}
            if (
                not _is_patch_omitted(parsed_blocks)
                and parsed_blocks is not None
            ):
                revision_update["parsed_blocks"] = parsed_blocks
            if not _is_patch_omitted(page_count) and page_count is not None:
                revision_update["page_count"] = page_count
            if not _is_patch_omitted(error):
                revision_update["error"] = error
            if not _is_patch_omitted(warning):
                revision_update["warning"] = warning
            self.store.revisions[revision.id] = revision.model_copy(
                update=revision_update
            )
        return self.store.manifest[index]

    async def publish_candidate(
        self,
        version_id,
        *,
        knowledge_base_id,
        expected_active_version_id,
        state,
        capabilities,
        degraded_reasons,
        metrics,
    ):
        if self.store.fail_phase == "publish":
            return False
        assert self.store.kb.active_version_id == expected_active_version_id
        self.store.version = self.store.version.model_copy(
            update={
                "state": state,
                "capabilities": capabilities,
                "degraded_reasons": degraded_reasons,
                "metrics": metrics,
                "published_at": datetime.now(timezone.utc),
            }
        )
        self.store.kb.active_version_id = version_id
        return True

    async def fail_candidate(
        self,
        version_id,
        *,
        knowledge_base_id,
        metrics=None,
    ):
        if self.store.version.state is not KnowledgeVersionState.BUILDING:
            return False
        self.store.version = self.store.version.model_copy(
            update={
                "state": KnowledgeVersionState.FAILED,
                "metrics": metrics or {},
            }
        )
        return True


class _RunnerKbRepo:
    def __init__(self, store: _RunnerStore) -> None:
        self.store = store

    async def get_kb(self, kb_id, scope=None):
        del scope
        return self.store.kb if kb_id == "kb-1" else None

    async def get_document(self, document_id):
        return self.store.documents.get(document_id)

    async def get_document_for_build(self, document_id):
        return self.store.documents.get(document_id)

    async def save_kb(self, kb):
        self.store.kb = kb

    async def replace_candidate_chunks(self, kb_id, version_id, chunks):
        if self.store.fail_phase == "keyword_index":
            raise RuntimeError("injected keyword failure")
        assert (kb_id, version_id) == ("kb-1", self.store.version.id)
        assert all(
            chunk.version_id == self.store.version.id for chunk in chunks
        )
        self.store.chunks = list(chunks)

    async def clone_version_chunks(
        self,
        kb_id,
        source_version_id,
        target_version_id,
        document_ids,
    ):
        assert (kb_id, source_version_id, target_version_id) == (
            "kb-1",
            self.store.parent_version.id,
            self.store.version.id,
        )
        assert set(document_ids)
        id_map = {
            chunk.id: f"{target_version_id}-clone-{chunk.id}"
            for chunk in self.store.source_chunks
            if chunk.doc_id in set(document_ids)
        }
        return [
            chunk.model_copy(
                update={
                    "id": id_map[chunk.id],
                    "version_id": target_version_id,
                    "parent_id": (
                        id_map[chunk.parent_id]
                        if chunk.parent_id is not None
                        else None
                    ),
                }
            )
            for chunk in self.store.source_chunks
            if chunk.doc_id in set(document_ids)
        ]

    async def replace_candidate_graph(
        self,
        kb_id,
        version_id,
        entities,
        relations,
        refs,
    ):
        assert (kb_id, version_id) == ("kb-1", self.store.version.id)
        self.store.graph = (
            list(entities),
            list(relations),
            list(refs),
        )

    async def get_candidate_index_metrics(self, kb_id, version_id):
        assert (kb_id, version_id) == ("kb-1", self.store.version.id)
        indexed = sum(
            item.state is DocumentRevisionState.INDEXED
            for item in self.store.manifest
        )
        failed = sum(
            item.state is DocumentRevisionState.FAILED
            for item in self.store.manifest
        )
        children = [
            chunk
            for chunk in self.store.chunks
            if chunk.level is ChunkLevel.CHILD
        ]
        parents = [
            chunk
            for chunk in self.store.chunks
            if chunk.level is ChunkLevel.PARENT
        ]
        if self.store.fail_phase == "validate":
            children = []
        return {
            "document_count": len(self.store.manifest),
            "indexed_document_count": indexed,
            "failed_document_count": failed,
            "parent_chunk_count": len(parents),
            "child_chunk_count": len(children),
            "vector_chunk_count": sum(
                bool(chunk.embedding)
                for chunk in children
            ),
            "entity_count": 0,
            "relation_count": 0,
            "entity_ref_count": 0,
        }

    def __getattr__(self, name):
        if name in {
            "clear_index_data",
            "purge_documents_index_data",
            "delete_document",
        }:
            self.store.forbidden_calls.append(name)
            raise AssertionError(f"forbidden destructive call: {name}")
        raise AttributeError(name)


class _RunnerResourceRepo:
    def __init__(self, store: _RunnerStore) -> None:
        self.store = store

    async def get_build(self, build_id, *, for_update=False):
        del for_update
        return self.store.build if build_id == self.store.build.id else None


class _RunnerUow:
    def __init__(self, store: _RunnerStore) -> None:
        self.store = store
        self.knowledge_base = _RunnerKbRepo(store)
        self.knowledge_version = _RunnerVersionRepo(store)
        self.resource_governance = _RunnerResourceRepo(store)

    async def __aenter__(self):
        self.snapshot = (
            self.store.kb.model_copy(deep=True),
            self.store.version.model_copy(deep=True),
        )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc, tb
        if exc_type is not None:
            self.store.kb, self.store.version = self.snapshot
            return False
        if (
            self.store.fail_phase == "publish_commit"
            and not self.store.commit_injected
            and self.store.kb.active_version_id == "v2"
        ):
            self.store.kb, self.store.version = self.snapshot
            self.store.commit_injected = True
            raise RuntimeError("injected publish commit failure")
        if (
            self.store.fail_phase == "publish_commit_uncertain"
            and not self.store.commit_injected
            and self.store.kb.active_version_id == "v2"
        ):
            self.store.commit_injected = True
            raise RuntimeError("injected uncertain publish acknowledgement")
        return False


class _RunnerBuildService:
    def __init__(self, store: _RunnerStore) -> None:
        self.store = store

    async def require_build(self, build_id, scope):
        del scope
        assert build_id == self.store.build.id
        return self.store.build

    async def append_event(
        self,
        build_id,
        *,
        phase,
        state,
        progress,
        payload,
        scope,
        resource_kind,
        resource_id,
        version_id,
    ):
        del scope
        assert (
            build_id,
            resource_kind,
            resource_id,
            version_id,
        ) == (
            self.store.build.id,
            ResourceKind.KNOWLEDGE_BASE,
            "kb-1",
            self.store.version.id,
        )
        if self.store.build.state in {
            BuildState.SUCCEEDED,
            BuildState.DEGRADED,
            BuildState.FAILED,
            BuildState.CANCELLED,
        }:
            previous = self.store.events[-1]
            if (
                previous.phase,
                previous.state,
                previous.progress,
                previous.payload,
            ) == (phase, state, progress, payload):
                return previous
            raise RuntimeError("terminal rejects event")
        if (
            self.store.fail_phase == "cancel_before_terminal"
            and phase == "publish"
            and state in {BuildState.SUCCEEDED, BuildState.DEGRADED}
            and not self.store.commit_injected
        ):
            self.store.commit_injected = True
            raise asyncio.CancelledError()
        event = ResourceBuildEvent(
            build_id=build_id,
            seq=len(self.store.events) + 1,
            phase=phase,
            state=state,
            progress=progress,
            payload=payload,
        )
        self.store.events.append(event)
        self.store.build = self.store.build.model_copy(
            update={
                "state": state,
                "phase": phase,
                "progress": progress,
                "last_event_seq": event.seq,
                "metrics": (
                    dict(payload["metrics"])
                    if isinstance(payload.get("metrics"), dict)
                    else self.store.build.metrics
                ),
            }
        )
        if (
            self.store.fail_phase == "cancel_after_parse"
            and phase == "parse"
            and progress >= 0.20
        ):
            raise asyncio.CancelledError()
        return event

    async def append_event_authoritative(
        self,
        build_id,
        *,
        phase,
        state,
        progress,
        payload,
        resource_kind,
        resource_id,
        version_id,
    ):
        return await self.append_event(
            build_id,
            phase=phase,
            state=state,
            progress=progress,
            payload=payload,
            scope=None,
            resource_kind=resource_kind,
            resource_id=resource_id,
            version_id=version_id,
        )


class _DeterministicChunker:
    def __init__(self, *args, **kwargs):
        del args, kwargs
        self._vector = SimpleNamespace(enabled=True)

    async def build_chunks(
        self,
        kb_id,
        doc_id,
        blocks,
        *,
        version_id,
    ):
        if _ACTIVE_RUNNER_STORE.fail_phase == "chunk":
            raise RuntimeError("injected chunk failure")
        if _ACTIVE_RUNNER_STORE.fail_phase == "cancel_chunk":
            raise asyncio.CancelledError()
        assert blocks
        parent = KnowledgeChunk(
            id=f"{version_id}-{doc_id}-p",
            kb_id=kb_id,
            doc_id=doc_id,
            version_id=version_id,
            level=ChunkLevel.PARENT,
            content="parent",
        )
        child = KnowledgeChunk(
            id=f"{version_id}-{doc_id}-c",
            kb_id=kb_id,
            doc_id=doc_id,
            version_id=version_id,
            parent_id=parent.id,
            level=ChunkLevel.CHILD,
            content="known result",
            embedding=(
                []
                if _ACTIVE_RUNNER_STORE.fail_phase == "vector"
                else [0.1, 0.2]
            ),
        )
        return [parent], [child]


_ACTIVE_RUNNER_STORE = _RunnerStore()


async def _run_candidate(
    monkeypatch,
    store: _RunnerStore,
    *,
    transition_calls: list | None = None,
):
    global _ACTIVE_RUNNER_STORE
    _ACTIVE_RUNNER_STORE = store
    config = AppConfig()
    config.knowledge_base.graphrag.enabled = (
        store.fail_phase == "graph"
        or str(store.fail_phase).startswith("graph_health_")
        or store.fail_phase
        in {"graph_checkpoint_fresh", "graph_checkpoint_resume"}
    )
    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner."
        "get_runtime_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "app.domain.services.knowledge_base.ingestion_runner.KBChunker",
        _DeterministicChunker,
    )
    if str(store.fail_phase).startswith("graph_health_"):
        class _HealthBuilder:
            def __init__(self, *args, **kwargs):
                del args, kwargs

            async def build(self, *args, **kwargs):
                del args, kwargs
                outcomes = {
                    "graph_health_partial": GraphBuildResult(
                        attempted=2,
                        succeeded=1,
                        failed=1,
                        invalid=0,
                        skipped=0,
                        entity_count=0,
                        relation_count=0,
                        persisted=True,
                        warning="GRAPH_INCOMPLETE",
                    ),
                    "graph_health_invalid": GraphBuildResult(
                        attempted=1,
                        succeeded=0,
                        failed=0,
                        invalid=1,
                        skipped=0,
                        entity_count=0,
                        relation_count=0,
                        persisted=True,
                        warning="GRAPH_INCOMPLETE",
                    ),
                    "graph_health_persistence": GraphBuildResult(
                        attempted=1,
                        succeeded=1,
                        failed=0,
                        invalid=0,
                        skipped=0,
                        entity_count=0,
                        relation_count=0,
                        persisted=False,
                        persistence_error="write failed",
                        warning="GRAPH_PERSISTENCE_FAILED",
                    ),
                }
                return outcomes[store.fail_phase]

        monkeypatch.setattr(
            "app.domain.services.knowledge_base.ingestion_runner."
            "GraphBuilder",
            _HealthBuilder,
        )
    elif store.fail_phase in {
        "graph_checkpoint_fresh",
        "graph_checkpoint_resume",
    }:
        class _ResumeBuilder:
            def __init__(self, *args, **kwargs):
                del args, kwargs

            async def build(self, *args, **kwargs):
                del args
                store.graph_build_kwargs = dict(kwargs)
                store.graph_metrics_before_build = dict(
                    store.build.metrics
                )
                await kwargs["checkpoint"](
                    {
                        "graph_cursor": "cursor-new",
                        "graph_processed_count": 2,
                        "graph_llm_call_count": 2,
                        "graph_token_count": 9,
                        "graph_actual_token_count": 9,
                        "graph_reserved_token_count": 9,
                        "graph_admitted_chunk_count": 2,
                        "graph_succeeded_count": 2,
                        "graph_failed_count": 0,
                        "graph_invalid_count": 0,
                    }
                )
                return GraphBuildResult(
                    attempted=2,
                    succeeded=2,
                    failed=0,
                    invalid=0,
                    skipped=0,
                    entity_count=0,
                    relation_count=0,
                    persisted=True,
                    processed=2,
                    calls=2,
                    tokens=9,
                    reserved_tokens=9,
                    cursor="cursor-new",
                )

        monkeypatch.setattr(
            "app.domain.services.knowledge_base.ingestion_runner."
            "GraphBuilder",
            _ResumeBuilder,
        )
    runner = KBIngestionRunner(
        uow_factory=lambda: _RunnerUow(store),
        file_storage=MagicMock(),
        llm=(
            MagicMock()
            if (
                str(store.fail_phase).startswith("graph_health_")
                or store.fail_phase
                in {"graph_checkpoint_fresh", "graph_checkpoint_resume"}
            )
            else None
        ),
        json_parser=(
            MagicMock()
            if (
                str(store.fail_phase).startswith("graph_health_")
                or store.fail_phase
                in {"graph_checkpoint_fresh", "graph_checkpoint_resume"}
            )
            else None
        ),
        build_service=_RunnerBuildService(store),
    )

    async def parse(doc):
        if (
            store.fail_phase == "parse"
            or (
                store.fail_phase == "partial_parse"
                and doc.id == "doc-1"
            )
        ):
            raise ValueError("injected parse failure")
        return [
            PageBlock(
                page_no=1,
                heading_path=doc.title,
                text="known result",
            )
        ], 1, store.parse_warning

    monkeypatch.setattr(runner, "_parse_document", parse)
    if transition_calls is not None:
        transition = runner._transition

        async def record_transition(*args, **kwargs):
            transition_calls.append((args, dict(kwargs)))
            return await transition(*args, **kwargs)

        monkeypatch.setattr(runner, "_transition", record_transition)
    events = [event async for event in runner.run(store.build.id)]
    return runner, events


@pytest.mark.asyncio
async def test_failed_retry_call_site_clears_old_diagnostics_then_preserves_new_warning(
    monkeypatch,
):
    store = _RunnerStore()
    revision = store.revisions["rev-0"]
    store.revisions["rev-0"] = revision.model_copy(
        update={
            "state": DocumentRevisionState.FAILED,
            "error": "stale error",
            "warning": "stale warning",
        }
    )
    store.manifest[0] = store.manifest[0].model_copy(
        update={
            "state": DocumentRevisionState.FAILED,
            "error": "stale error",
            "warning": "stale warning",
        }
    )
    store.parse_warning = "new parse warning"
    transition_calls = []

    _runner, events = await _run_candidate(
        monkeypatch,
        store,
        transition_calls=transition_calls,
    )

    parsing_call = next(
        kwargs
        for args, kwargs in transition_calls
        if args[2] is DocumentRevisionState.PARSING
    )
    assert "error" in parsing_call
    assert parsing_call["error"] is None
    assert "warning" in parsing_call
    assert parsing_call["warning"] is None
    assert events[-1].type == "done"
    assert store.manifest[0].error is None
    assert store.manifest[0].warning == "new parse warning"
    assert store.revisions["rev-0"].error is None
    assert store.revisions["rev-0"].warning == "new parse warning"


@pytest.mark.asyncio
async def test_success_publishes_candidate_after_validation_once(monkeypatch):
    store = _RunnerStore()
    runner, events = await _run_candidate(monkeypatch, store)

    assert events[-1].type == "done"
    assert store.kb.active_version_id == "v2"
    assert store.version.state is KnowledgeVersionState.READY
    assert store.version.capabilities == {
        "keyword_search": True,
        "vector_search": True,
        "graph_search": False,
    }
    assert {chunk.version_id for chunk in store.chunks} == {"v2"}
    assert store.forbidden_calls == []
    assert [event.phase for event in store.events] == [
        "parse",
        "parse",
        "chunk",
        "keyword_index",
        "vector_index",
        "graph",
        "validate",
        "publish",
    ]
    assert sum(
        event.state
        in {
            BuildState.SUCCEEDED,
            BuildState.DEGRADED,
            BuildState.FAILED,
            BuildState.CANCELLED,
        }
        for event in store.events
    ) == 1

    before = list(store.events)
    assert [event async for event in runner.run("build-1")] == []
    assert store.events == before


@pytest.mark.asyncio
async def test_graph_deadline_is_persisted_before_first_builder_call(
    monkeypatch,
):
    store = _RunnerStore()
    store.fail_phase = "graph_checkpoint_fresh"

    _runner, events = await _run_candidate(monkeypatch, store)

    assert events[-1].type == "done"
    persisted = store.graph_metrics_before_build
    deadline = datetime.fromisoformat(persisted["graph_deadline_utc"])
    assert deadline.tzinfo is not None
    assert deadline > datetime.now(timezone.utc)
    assert persisted["graph_deadline_version_id"] == "v2"
    assert (
        store.graph_build_kwargs["deadline_utc"]
        == persisted["graph_deadline_utc"]
    )


@pytest.mark.asyncio
async def test_graph_checkpoint_reloads_and_resumes_without_chunk_replace(
    monkeypatch,
):
    store = _RunnerStore()
    store.fail_phase = "graph_checkpoint_resume"
    store.build = store.build.model_copy(
        update={
            "state": BuildState.RUNNING,
            "phase": "graph",
            "progress": 0.7,
            "metrics": {
                "graph_cursor": "cursor-old",
                "graph_deadline_utc": "2099-01-01T00:00:00+00:00",
                "graph_deadline_version_id": "v2",
                "graph_processed_count": 3,
                "graph_llm_call_count": 3,
                "graph_token_count": 12,
                "graph_succeeded_count": 3,
                "graph_failed_count": 0,
                "graph_invalid_count": 0,
            },
        }
    )
    store.chunks = [
        KnowledgeChunk(
            id="v2-doc-0-p",
            kb_id="kb-1",
            doc_id="doc-0",
            version_id="v2",
            level=ChunkLevel.PARENT,
            content="parent",
        ),
        KnowledgeChunk(
            id="v2-doc-0-c",
            kb_id="kb-1",
            doc_id="doc-0",
            version_id="v2",
            parent_id="v2-doc-0-p",
            level=ChunkLevel.CHILD,
            content="known result",
            embedding=[0.1, 0.2],
        ),
    ]

    _runner, events = await _run_candidate(monkeypatch, store)

    assert events[-1].type == "done"
    assert store.graph_build_kwargs["resume_cursor"] == "cursor-old"
    assert (
        store.graph_build_kwargs["deadline_utc"]
        == "2099-01-01T00:00:00+00:00"
    )
    assert store.graph_build_kwargs["consumed_chunks"] == 3
    assert store.graph_build_kwargs["consumed_llm_calls"] == 3
    assert store.graph_build_kwargs["consumed_tokens"] == 12
    assert store.build.metrics["graph_cursor"] == "cursor-new"
    assert store.build.metrics["graph_processed_count"] == 5
    assert store.build.metrics["graph_llm_call_count"] == 5
    assert store.build.metrics["graph_token_count"] == 21
    assert store.build.metrics["graph_actual_token_count"] == 21
    assert store.build.metrics["graph_reserved_token_count"] == 21
    assert store.build.metrics["graph_admitted_chunk_count"] == 5
    assert store.build.metrics["graph_attempted_count"] == 5
    assert store.build.metrics["graph_succeeded_count"] == 5


@pytest.mark.asyncio
async def test_one_document_parse_failure_publishes_partial_degraded(
    monkeypatch,
):
    store = _RunnerStore(documents=2)
    store.fail_phase = "partial_parse"

    _runner, events = await _run_candidate(monkeypatch, store)

    assert events[-1].type == "done"
    assert store.kb.active_version_id == "v2"
    assert store.version.state is KnowledgeVersionState.DEGRADED
    assert list(store.version.degraded_reasons) == ["DOCUMENT_PARTIAL"]
    assert store.version.capabilities["keyword_search"] is True
    assert store.version.capabilities["vector_search"] is True
    assert {
        item.document_id
        for item in store.manifest
        if item.state is DocumentRevisionState.FAILED
    } == {"doc-1"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failing_phase",
    [
        "parse",
        "chunk",
        "keyword_index",
        "validate",
        "publish",
        "publish_commit",
    ],
)
async def test_mandatory_phase_failure_keeps_old_active_and_one_terminal(
    monkeypatch,
    failing_phase,
):
    store = _RunnerStore()
    store.fail_phase = failing_phase

    _runner, events = await _run_candidate(monkeypatch, store)

    assert events[-1].type == "error"
    assert store.kb.active_version_id == "v1"
    assert store.version.state is KnowledgeVersionState.FAILED
    assert store.build.state is BuildState.FAILED
    assert sum(
        event.state
        in {
            BuildState.SUCCEEDED,
            BuildState.DEGRADED,
            BuildState.FAILED,
            BuildState.CANCELLED,
        }
        for event in store.events
    ) == 1
    assert store.forbidden_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("optional_phase", "reason", "capability"),
    [
        ("vector", "EMBEDDING_UNAVAILABLE", "vector_search"),
        ("graph", "GRAPH_UNAVAILABLE", "graph_search"),
    ],
)
async def test_optional_index_failure_publishes_truthful_degraded_version(
    monkeypatch,
    optional_phase,
    reason,
    capability,
):
    store = _RunnerStore()
    store.fail_phase = optional_phase

    _runner, events = await _run_candidate(monkeypatch, store)

    assert events[-1].type == "done"
    assert store.kb.active_version_id == "v2"
    assert store.version.state is KnowledgeVersionState.DEGRADED
    assert reason in store.version.degraded_reasons
    assert store.version.capabilities["keyword_search"] is True
    assert store.version.capabilities[capability] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "graph_health_partial",
        "graph_health_invalid",
        "graph_health_persistence",
    ],
)
async def test_incomplete_graph_health_never_advertises_capability(
    monkeypatch,
    mode,
):
    store = _RunnerStore()
    store.fail_phase = mode

    _runner, events = await _run_candidate(monkeypatch, store)

    assert events[-1].type == "done"
    assert store.version.state is KnowledgeVersionState.DEGRADED
    assert store.version.capabilities["graph_search"] is False
    assert list(store.version.degraded_reasons).count(
        "GRAPH_UNAVAILABLE"
    ) == 1
    assert store.version.metrics["graph_attempted_count"] >= 1


@pytest.mark.asyncio
async def test_parsed_revision_is_not_visible_when_cancelled_before_index(
    monkeypatch,
):
    store = _RunnerStore()
    store.fail_phase = "cancel_after_parse"

    with pytest.raises(asyncio.CancelledError):
        await _run_candidate(monkeypatch, store)

    assert store.kb.active_version_id == "v1"
    assert store.chunks == []
    assert store.revisions["rev-0"].parsed_blocks
    assert store.revisions["rev-0"].state is DocumentRevisionState.PARSED
    assert store.build.state is BuildState.CANCELLED
    assert sum(
        event.state is BuildState.CANCELLED
        for event in store.events
    ) == 1


@pytest.mark.asyncio
async def test_reentry_repairs_missing_terminal_after_publish(monkeypatch):
    store = _RunnerStore()
    store.kb.active_version_id = "v2"
    store.version = store.version.model_copy(
        update={
            "state": KnowledgeVersionState.READY,
            "capabilities": {
                "keyword_search": True,
                "vector_search": True,
                "graph_search": False,
            },
            "metrics": {"child_chunk_count": 1},
            "published_at": datetime.now(timezone.utc),
        }
    )

    _runner, events = await _run_candidate(monkeypatch, store)

    assert events[-1].type == "done"
    assert len(store.events) == 1
    assert store.events[0].phase == "publish"
    assert store.events[0].state is BuildState.SUCCEEDED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "publish_commit_uncertain",
        "cancel_before_terminal",
    ],
)
async def test_publish_commit_uncertainty_or_cancellation_repairs_success(
    monkeypatch,
    mode,
):
    store = _RunnerStore()
    store.fail_phase = mode

    if mode == "cancel_before_terminal":
        with pytest.raises(asyncio.CancelledError):
            await _run_candidate(monkeypatch, store)
    else:
        _runner, events = await _run_candidate(monkeypatch, store)
        assert events[-1].type == "done"

    assert store.kb.active_version_id == "v2"
    assert store.version.state is KnowledgeVersionState.READY
    assert store.build.state is BuildState.SUCCEEDED
    terminal = [
        event
        for event in store.events
        if event.state
        in {
            BuildState.SUCCEEDED,
            BuildState.DEGRADED,
            BuildState.FAILED,
            BuildState.CANCELLED,
        }
    ]
    assert len(terminal) == 1
    assert terminal[0].payload["metrics"] == dict(store.version.metrics)


@pytest.mark.asyncio
async def test_cancel_published_candidate_repairs_success_terminal(
    monkeypatch,
):
    store = _RunnerStore()
    store.kb.active_version_id = "v2"
    store.version = store.version.model_copy(
        update={
            "state": KnowledgeVersionState.READY,
            "capabilities": {
                "keyword_search": True,
                "vector_search": True,
                "graph_search": False,
            },
            "metrics": {"child_chunk_count": 1},
            "published_at": datetime.now(timezone.utc),
        }
    )
    runner, _events = await _run_candidate(monkeypatch, store)
    # Re-open the build-only missing-terminal window.
    store.events.clear()
    store.build = store.build.model_copy(
        update={
            "state": BuildState.RUNNING,
            "phase": "validate",
            "progress": 0.88,
            "last_event_seq": 0,
        }
    )

    await runner.cancel("build-1")

    assert store.build.state is BuildState.SUCCEEDED
    assert len(store.events) == 1
    assert store.events[0].phase == "publish"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "documents"),
    [
        ("remove", 1),
        ("reindex", 1),
        ("add", 2),
    ],
)
async def test_indexed_empty_blocks_clone_without_mutating_source_version(
    monkeypatch,
    operation,
    documents,
):
    store = _RunnerStore(documents=documents)
    legacy_revision = store.revisions["rev-0"].model_copy(
        update={
            "state": DocumentRevisionState.INDEXED,
            "parsed_blocks": (),
            "needs_chunk_clone": True,
        }
    )
    store.revisions["rev-0"] = legacy_revision
    store.manifest[0] = store.manifest[0].model_copy(
        update={"state": DocumentRevisionState.INDEXED}
    )
    source_parent = KnowledgeChunk(
        id="v1-parent",
        kb_id="kb-1",
        doc_id="doc-0",
        version_id="v1",
        level=ChunkLevel.PARENT,
        content="legacy parent",
    )
    source_child = KnowledgeChunk(
        id="v1-child",
        kb_id="kb-1",
        doc_id="doc-0",
        version_id="v1",
        parent_id=source_parent.id,
        level=ChunkLevel.CHILD,
        content="legacy child",
        embedding=[0.1, 0.2],
    )
    store.source_chunks = [source_parent, source_child]
    v1_bytes = (
        legacy_revision.model_dump_json(),
        store.manifest[0].model_dump_json(),
        [chunk.model_dump_json() for chunk in store.source_chunks],
    )

    _runner, events = await _run_candidate(monkeypatch, store)

    assert operation in {"remove", "reindex", "add"}
    assert events[-1].type == "done"
    assert store.kb.active_version_id == "v2"
    assert {
        chunk.doc_id
        for chunk in store.chunks
        if chunk.level is ChunkLevel.CHILD
    } == {f"doc-{idx}" for idx in range(documents)}
    assert (
        store.revisions["rev-0"].model_dump_json(),
        store.manifest[0].model_dump_json(),
        [chunk.model_dump_json() for chunk in store.source_chunks],
    ) == v1_bytes


@pytest.mark.asyncio
async def test_marked_legacy_revision_clones_across_two_published_generations(
    monkeypatch,
):
    # v2 adds doc-1 while retaining the clone-marked doc-0.
    first = _RunnerStore(documents=2)
    first.revisions["rev-0"] = first.revisions["rev-0"].model_copy(
        update={
            "state": DocumentRevisionState.INDEXED,
            "parsed_blocks": (),
            "needs_chunk_clone": True,
        }
    )
    first.manifest[0] = first.manifest[0].model_copy(
        update={"state": DocumentRevisionState.INDEXED}
    )
    source_parent = KnowledgeChunk(
        id="v1-parent",
        kb_id="kb-1",
        doc_id="doc-0",
        version_id="v1",
        level=ChunkLevel.PARENT,
        content="legacy parent",
    )
    source_child = KnowledgeChunk(
        id="v1-child",
        kb_id="kb-1",
        doc_id="doc-0",
        version_id="v1",
        parent_id=source_parent.id,
        level=ChunkLevel.CHILD,
        content="legacy child",
        segmented_content="遗留 分词",
        content_tsv="'遗留':1 '分词':2",
        embedding=[0.1, 0.2],
    )
    first.source_chunks = [source_parent, source_child]
    v1_bytes = (
        first.revisions["rev-0"].model_dump_json(),
        [chunk.model_dump_json() for chunk in first.source_chunks],
    )

    _first_runner, first_events = await _run_candidate(monkeypatch, first)
    assert first_events[-1].type == "done"

    second = _RunnerStore()
    second.kb = first.kb.model_copy(
        update={"ingest_task_id": "build-2"}
    )
    second.build = first.build.model_copy(
        update={
            "id": "build-2",
            "version_id": "v3",
            "parent_version_id": "v2",
            "state": BuildState.QUEUED,
            "phase": "queued",
            "progress": 0.0,
        }
    )
    second.parent_version = first.version
    second.version = KnowledgeBaseVersion(
        id="v3",
        knowledge_base_id="kb-1",
        parent_version_id="v2",
        build_id="build-2",
    )
    second.revisions = dict(first.revisions)
    second.manifest = [
        first.manifest[0].model_copy(update={"version_id": "v3"})
    ]
    second.documents = dict(first.documents)
    second.source_chunks = list(first.chunks)

    _second_runner, second_events = await _run_candidate(
        monkeypatch,
        second,
    )

    assert second_events[-1].type == "done"
    assert second.kb.active_version_id == "v3"
    assert second.revisions["rev-0"].needs_chunk_clone is True
    assert {
        chunk.version_id for chunk in second.chunks
    } == {"v3"}
    assert (
        first.revisions["rev-0"].model_dump_json(),
        [chunk.model_dump_json() for chunk in first.source_chunks],
    ) == v1_bytes

    # v4 represents a no-content-change reindex candidate. Its immediate
    # parent is non-legacy, but the immutable revision marker still permits
    # exact reuse.
    third = _RunnerStore()
    third.kb = second.kb.model_copy(
        update={"ingest_task_id": "build-3"}
    )
    third.build = second.build.model_copy(
        update={
            "id": "build-3",
            "version_id": "v4",
            "parent_version_id": "v3",
            "state": BuildState.QUEUED,
            "phase": "queued",
            "progress": 0.0,
        }
    )
    third.parent_version = second.version
    third.version = KnowledgeBaseVersion(
        id="v4",
        knowledge_base_id="kb-1",
        parent_version_id="v3",
        build_id="build-3",
    )
    third.revisions = {"rev-0": second.revisions["rev-0"]}
    third.manifest = [
        second.manifest[0].model_copy(update={"version_id": "v4"})
    ]
    third.documents = {"doc-0": second.documents["doc-0"]}
    third.source_chunks = list(second.chunks)

    _third_runner, third_events = await _run_candidate(
        monkeypatch,
        third,
    )

    assert third_events[-1].type == "done"
    assert third.kb.active_version_id == "v4"
    assert third.revisions["rev-0"].needs_chunk_clone is True
    assert {chunk.version_id for chunk in third.chunks} == {"v4"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("persisted_phase", "progress"),
    [
        ("parse", 0.20),
        ("keyword_index", 0.52),
        ("graph", 0.76),
        ("validate", 0.88),
        ("publish", 0.95),
    ],
)
async def test_reentry_does_not_emit_phase_before_persisted_rank(
    monkeypatch,
    persisted_phase,
    progress,
):
    store = _RunnerStore()
    store.build = store.build.model_copy(
        update={
            "state": BuildState.RUNNING,
            "phase": persisted_phase,
            "progress": progress,
        }
    )

    _runner, events = await _run_candidate(monkeypatch, store)

    assert events[-1].type == "done"
    phases = [event.phase for event in store.events]
    order = [
        "parse",
        "chunk",
        "keyword_index",
        "vector_index",
        "graph",
        "validate",
        "publish",
    ]
    assert phases
    assert all(
        order.index(phase) >= order.index(persisted_phase)
        for phase in phases
    )
    assert phases[-1] == "publish"


@pytest.mark.asyncio
async def test_missing_candidate_orphan_closes_once_without_owner_lookup(
    monkeypatch,
):
    store = _RunnerStore()
    store.missing_candidate = True

    _runner, first = await _run_candidate(monkeypatch, store)
    _runner, second = await _run_candidate(monkeypatch, store)

    assert first[-1].type == "error"
    assert second[-1].type == "error"
    assert store.build.state is BuildState.FAILED
    terminal = [
        event
        for event in store.events
        if event.state is BuildState.FAILED
    ]
    assert len(terminal) == 1
    assert terminal[0].payload["error_code"] == "BUILD_CLOSURE_INVALID"
