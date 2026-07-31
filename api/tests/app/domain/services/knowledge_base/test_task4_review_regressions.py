#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Strict review regressions for versioned KB ingestion Task 4."""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from app.application.services.resource_build_service import (
    ResourceBuildService,
)
from app.domain.models.knowledge_base import (
    ChunkLevel,
    KnowledgeChunk,
)
from app.domain.models.resource_governance import (
    ResourceKind,
    build_phase_regresses,
)
from app.domain.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from app.domain.services.knowledge_base.graph_builder import (
    GraphBuildResult,
    GraphBuilder,
)
from app.infrastructure.repositories.db_knowledge_base_repository import (
    DBKnowledgeBaseRepository,
)


class _GraphRepo:
    def __init__(self, *, persist_error: Exception | None = None) -> None:
        self.persist_error = persist_error
        self.graph = None

    async def replace_candidate_graph(
        self,
        kb_id,
        version_id,
        entities,
        relations,
        refs,
    ):
        if self.persist_error is not None:
            raise self.persist_error
        self.graph = (
            kb_id,
            version_id,
            list(entities),
            list(relations),
            list(refs),
        )


class _GraphUow:
    def __init__(self, repo: _GraphRepo) -> None:
        self.knowledge_base = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SequenceLLM:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)

    async def invoke(self, messages):
        del messages
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return {"content": outcome}


class _JsonParser:
    async def invoke(self, text, default_value=None):
        del default_value
        if text == "valid":
            return {
                "entities": [
                    {
                        "name": "OpenCitadel",
                        "type": "product",
                        "description": "",
                    }
                ],
                "relations": [],
            }
        if text == "empty":
            return {"entities": [], "relations": []}
        return []


def _parent(index: int) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=f"p-{index}",
        kb_id="kb-1",
        doc_id=f"doc-{index}",
        version_id="v2",
        level=ChunkLevel.PARENT,
        content=f"parent {index}",
    )


def test_legacy_clone_and_active_document_seams_are_explicit_contracts():
    assert callable(
        getattr(KnowledgeBaseRepository, "clone_version_chunks", None)
    )
    assert callable(
        getattr(KnowledgeBaseRepository, "get_document_for_build", None)
    )


def test_every_existing_public_read_query_is_active_version_scoped():
    methods = (
        "list_documents",
        "get_document",
        "count_documents",
        "vector_search_chunks",
        "bm25_search_chunks",
        "get_parents_by_ids",
        "get_chunks_by_ids",
        "list_chunks_for_document",
        "count_ready_documents",
        "count_child_chunks",
        "list_documents_page",
        "list_entities",
        "list_relations_for_entities",
        "get_related_chunk_ids",
    )
    for name in methods:
        source = inspect.getsource(
            getattr(DBKnowledgeBaseRepository, name)
        )
        assert (
            "active_version_id" in source
            or "_active_version_row_predicate" in source
            or "_active_document_predicate" in source
        ), name


def test_shared_phase_rank_rejects_kb_regression_but_not_reentry_equal():
    assert not build_phase_regresses(
        ResourceKind.KNOWLEDGE_BASE,
        "graph",
        "graph",
    )
    assert build_phase_regresses(
        ResourceKind.KNOWLEDGE_BASE,
        "graph",
        "parse",
    )
    assert build_phase_regresses(
        ResourceKind.KNOWLEDGE_BASE,
        "publish",
        "validate",
    )


def test_internal_authoritative_event_append_exists_for_orphan_closure():
    signature = inspect.signature(
        ResourceBuildService.append_event_authoritative
    )
    assert "build_id" in signature.parameters
    assert "resource_kind" in signature.parameters
    assert "resource_id" in signature.parameters
    assert "version_id" in signature.parameters


@pytest.mark.asyncio
async def test_graph_health_reports_total_partial_invalid_and_persistence_failure():
    scenarios = (
        (
            [RuntimeError("one"), RuntimeError("two")],
            (2, 0, 2, 0, False),
        ),
        (
            ["valid", RuntimeError("two")],
            (2, 1, 1, 0, False),
        ),
        (
            ["empty", "invalid"],
            (2, 0, 0, 2, False),
        ),
    )
    for outcomes, expected in scenarios:
        repo = _GraphRepo()
        result = await GraphBuilder(
            uow_factory=lambda: _GraphUow(repo),
            llm=_SequenceLLM(outcomes),
            json_parser=_JsonParser(),
        ).build("kb-1", [_parent(1), _parent(2)], version_id="v2")
        assert isinstance(result, GraphBuildResult)
        assert (
            result.attempted,
            result.succeeded,
            result.failed,
            result.invalid,
            result.complete,
        ) == expected
        assert result.persisted is True

    failed_repo = _GraphRepo(persist_error=RuntimeError("write failed"))
    failed = await GraphBuilder(
        uow_factory=lambda: _GraphUow(failed_repo),
        llm=_SequenceLLM(["valid"]),
        json_parser=_JsonParser(),
    ).build("kb-1", [_parent(1)], version_id="v2")
    assert failed.persisted is False
    assert failed.persistence_error == "write failed"
    assert failed.complete is False


def test_candidate_validation_contains_revision_and_per_document_closure():
    source = inspect.getsource(
        DBKnowledgeBaseRepository.get_candidate_index_metrics
    )
    for contract_fragment in (
        "indexed manifest requires child chunks",
        "failed manifest cannot own chunks",
        "manifest revision state mismatch",
        "manifest revision closure is incomplete",
    ):
        assert contract_fragment in source
