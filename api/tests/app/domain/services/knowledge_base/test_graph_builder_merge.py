#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.models.knowledge_base import ChunkLevel, KnowledgeChunk
from app.domain.services.knowledge_base.graph_builder import GraphBuilder


class _FakeKbRepo:
    def __init__(self, existing_ids: dict[str, str] | None = None):
        self._existing = existing_ids or {}
        self.upserted = []
        self.saved_relations = []
        self.saved_refs = []

    async def upsert_entities(self, entities):
        self.upserted = entities
        id_map = {}
        for entity in entities:
            key = entity.name.strip().lower()
            id_map[key] = self._existing.get(key, entity.id)
        return id_map

    async def save_relations(self, relations):
        self.saved_relations = relations

    async def save_entity_refs(self, refs):
        self.saved_refs = refs


class _FakeUow:
    def __init__(self, repo):
        self.knowledge_base = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeLLM:
    async def invoke(self, messages):
        return {
            "content": (
                '{"entities": [{"name": "OpenCitadel", "type": "产品", "description": ""},'
                ' {"name": "RAG", "type": "概念", "description": ""}],'
                ' "relations": [{"src": "OpenCitadel", "dst": "RAG", "relation": "使用"}]}'
            )
        }


class _FakeJsonParser:
    async def invoke(self, text, default_value=None):
        import json

        try:
            return json.loads(text)
        except Exception:
            return default_value


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _parent(doc_id: str) -> KnowledgeChunk:
    return KnowledgeChunk(kb_id="kb1", doc_id=doc_id, level=ChunkLevel.PARENT, content="OpenCitadel 使用 RAG")


@pytest.mark.anyio
async def test_build_merges_into_existing_entity_and_writes_refs():
    repo = _FakeKbRepo(existing_ids={"opencitadel": "e-old"})
    builder = GraphBuilder(uow_factory=lambda: _FakeUow(repo), llm=_FakeLLM(), json_parser=_FakeJsonParser())

    entity_count, relation_count, warning = await builder.build("kb1", [_parent("d1")])

    assert entity_count == 2
    assert relation_count == 1
    # 关系两端使用 upsert 返回的最终 id（合并到已有实体 e-old）
    relation = repo.saved_relations[0]
    assert relation.src_entity_id == "e-old"
    assert relation.dst_entity_id != "e-old"
    # 每个实体都有对来源文档的引用行
    assert {(ref.entity_id, ref.doc_id) for ref in repo.saved_refs} == {
        ("e-old", "d1"),
        (relation.dst_entity_id, "d1"),
    }


@pytest.mark.anyio
async def test_build_refs_cover_each_supporting_doc():
    repo = _FakeKbRepo()
    builder = GraphBuilder(uow_factory=lambda: _FakeUow(repo), llm=_FakeLLM(), json_parser=_FakeJsonParser())

    await builder.build("kb1", [_parent("d1"), _parent("d2")])

    docs_per_entity = {}
    for ref in repo.saved_refs:
        docs_per_entity.setdefault(ref.entity_id, set()).add(ref.doc_id)
    assert all(docs == {"d1", "d2"} for docs in docs_per_entity.values())
