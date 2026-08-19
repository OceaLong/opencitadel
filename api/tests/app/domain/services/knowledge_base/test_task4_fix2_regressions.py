#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Strict regressions for the second Task 4 review-fix round."""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

import pytest

from app.domain.models.knowledge_base import ChunkLevel, KnowledgeChunk
from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeDocumentRevision,
)
from app.domain.services.knowledge_base.graph_builder import GraphBuilder
from app.domain.services.knowledge_base.ingestion_runner import (
    KBIngestionRunner,
)
from app.infrastructure.models.knowledge_version import (
    KnowledgeDocumentRevisionORM,
)
from app.infrastructure.repositories.db_knowledge_base_repository import (
    DBKnowledgeBaseRepository,
)


class _GraphRepository:
    def __init__(self) -> None:
        self.graph = None

    async def replace_candidate_graph(
        self,
        kb_id,
        version_id,
        entities,
        relations,
        refs,
    ):
        self.graph = (
            kb_id,
            version_id,
            list(entities),
            list(relations),
            list(refs),
        )


class _GraphUnitOfWork:
    def __init__(self, repository: _GraphRepository) -> None:
        self.knowledge_base = repository

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _PayloadLLM:
    async def invoke(self, messages):
        del messages
        return {"content": "payload"}


class _PayloadParser:
    def __init__(self, payload) -> None:
        self.payload = payload

    async def invoke(self, text, default_value=None):
        del text, default_value
        return self.payload


def _parent() -> KnowledgeChunk:
    return KnowledgeChunk(
        id="parent",
        kb_id="kb",
        doc_id="doc",
        version_id="v2",
        level=ChunkLevel.PARENT,
        content="content",
    )


def test_legacy_chunk_clone_marker_round_trips_on_immutable_revision():
    revision = KnowledgeDocumentRevision(
        id="revision",
        document_id="document",
        source_digest="a" * 64,
        state=DocumentRevisionState.INDEXED,
        needs_chunk_clone=True,
    )

    persisted = KnowledgeDocumentRevisionORM.from_domain(revision)
    restored = persisted.to_domain()

    assert persisted.needs_chunk_clone is True
    assert restored.needs_chunk_clone is True
    assert restored.model_dump_json() == revision.model_dump_json()


def test_runner_uses_revision_clone_marker():
    source = inspect.getsource(KBIngestionRunner.run)

    assert "revision.needs_chunk_clone" in source
    assert "legacy_snapshot" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    (
        {
            "entities": [
                {
                    "name": "valid",
                    "type": "concept",
                    "description": "",
                },
                {"name": "missing fields"},
            ],
            "relations": [],
        },
        {
            "entities": [
                {
                    "name": "valid",
                    "type": "concept",
                    "description": "",
                }
            ],
            "relations": [{"src": "valid", "dst": 42, "relation": "bad"}],
        },
        {
            "entities": [
                {
                    "name": "valid",
                    "type": "concept",
                    "description": "",
                }
            ],
            "relations": [
                {
                    "src": "valid",
                    "dst": "missing",
                    "relation": "unresolved",
                }
            ],
        },
    ),
)
async def test_mixed_graph_payload_is_invalid_as_a_whole(payload):
    repository = _GraphRepository()
    result = await GraphBuilder(
        uow_factory=lambda: _GraphUnitOfWork(repository),
        llm=_PayloadLLM(),
        json_parser=_PayloadParser(payload),
    ).build("kb", [_parent()], version_id="v2")

    assert result.attempted == 1
    assert result.succeeded == 0
    assert result.invalid == 1
    assert result.complete is False
    assert result.entity_count == 0
    assert result.relation_count == 0
    assert repository.graph == ("kb", "v2", [], [], [])


def test_active_read_and_candidate_closure_are_single_contracts():
    repository_source = inspect.getsource(DBKnowledgeBaseRepository)
    validation_source = inspect.getsource(
        DBKnowledgeBaseRepository.get_candidate_index_metrics
    )

    assert "_active_version_row_predicate" in repository_source
    assert "legacy_snapshot" not in repository_source
    for fragment in (
        "parent.level <> 'parent'",
        "parent.kb_id <> child.kb_id",
        "parent.doc_id <> child.doc_id",
        "candidate failed manifest cannot own graph evidence",
        "manifest.state = 'failed'",
    ):
        assert fragment in validation_source


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one(self):
        return self.value


class _RowsResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def fetchall(self):
        return self.rows


class _CloneSession:
    def __init__(self) -> None:
        self.calls = []
        self.results = [
            _ScalarResult(1),
            _ScalarResult(1),
            _RowsResult(
                [
                    SimpleNamespace(
                        id="source-parent",
                        kb_id="kb",
                        doc_id="doc",
                        parent_id=None,
                        level="parent",
                        content="parent",
                        page_no=7,
                        heading_path="heading",
                        ordinal=2,
                        embedding_text=None,
                        content_tsv_text="'父':1",
                    ),
                    SimpleNamespace(
                        id="source-child",
                        kb_id="kb",
                        doc_id="doc",
                        parent_id="source-parent",
                        level="child",
                        content="child",
                        page_no=8,
                        heading_path="heading/child",
                        ordinal=3,
                        embedding_text="[0.1,0.2]",
                        content_tsv_text="'分':1 '词':2",
                    ),
                ]
            ),
        ]

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_clone_requires_exact_marked_manifest_and_preserves_search_index():
    session = _CloneSession()
    repository = DBKnowledgeBaseRepository(session)
    repository._require_building_candidate = _noop_candidate_check

    chunks = await repository.clone_version_chunks(
        "kb",
        "v2",
        "v3",
        ["doc"],
    )

    assert [chunk.id for chunk in chunks] == [
        str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "opencitadel:kb-chunk:v3:source-parent",
            )
        ),
        str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "opencitadel:kb-chunk:v3:source-child",
            )
        ),
    ]
    assert chunks[1].parent_id == chunks[0].id
    assert chunks[1].embedding == [0.1, 0.2]
    assert chunks[1].content_tsv == "'分':1 '词':2"
    assert (
        chunks[1].content,
        chunks[1].page_no,
        chunks[1].heading_path,
        chunks[1].ordinal,
    ) == ("child", 8, "heading/child", 3)
    ownership_sql, manifest_sql, select_sql = [
        sql for sql, _params in session.calls
    ]
    assert "source.legacy_snapshot" not in ownership_sql
    assert "kb.active_version_id = source.id" in ownership_sql
    assert "source_manifest.document_revision_id" in manifest_sql
    assert "revision.needs_chunk_clone IS TRUE" in manifest_sql
    assert "content_tsv::text AS content_tsv_text" in select_sql


async def _noop_candidate_check(_kb_id, _version_id):
    return None
