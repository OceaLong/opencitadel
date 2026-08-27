"""Strict regressions for the second Task 4 review-fix round."""

from __future__ import annotations

import inspect

import pytest

from app.domain.models.knowledge_base import ChunkLevel, KnowledgeChunk
from app.domain.services.knowledge_base.graph_builder import GraphBudget, GraphBuilder
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

    async def commit(self) -> None:
        return None


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
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
    ],
)
async def test_mixed_graph_payload_is_invalid_as_a_whole(payload):
    repository = _GraphRepository()
    result = await GraphBuilder(
        uow_factory=lambda: _GraphUnitOfWork(repository),
        llm=_PayloadLLM(),
        json_parser=_PayloadParser(payload),
        max_parent_chunks_per_doc=200,
        concurrency=1,
    ).build("kb", [_parent()], version_id="v2", budget=GraphBudget())

    assert result.attempted == 1
    assert result.succeeded == 0
    assert result.invalid == 1
    assert result.complete is False
    assert result.entity_count == 0
    assert result.relation_count == 0
    assert repository.graph == ("kb", "v2", [], [], [])


def test_active_read_and_candidate_closure_are_single_contracts():
    repository_source = inspect.getsource(DBKnowledgeBaseRepository)
    validation_source = inspect.getsource(DBKnowledgeBaseRepository.get_candidate_index_metrics)

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
