#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SQL-shape contracts for race-safe versioned graph persistence."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.models.knowledge_base import (
    KnowledgeEntity,
    KnowledgeEntityRef,
    KnowledgeRelation,
)
from app.infrastructure.repositories.db_knowledge_base_repository import (
    DBKnowledgeBaseRepository,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalars(self):
        values = self.value if isinstance(self.value, list) else []

        class _Scalars:
            def all(self):
                return values

        return _Scalars()


class _Session:
    def __init__(self, entity_ids):
        self.entity_ids = iter(entity_ids)
        self.calls = []
        self.flushes = 0

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        if "INSERT INTO knowledge_entities" in str(statement):
            return _Result(next(self.entity_ids))
        return _Result(None)

    async def flush(self):
        self.flushes += 1


class _Repo(DBKnowledgeBaseRepository):
    async def _require_building_candidate(self, kb_id, version_id):
        assert (kb_id, version_id) == ("kb1", "v1")
        return SimpleNamespace(id="v1")


def _batch():
    entities = [
        KnowledgeEntity(
            id="temp-product",
            kb_id="kb1",
            version_id="v1",
            name="OpenCitadel",
            normalized_name="opencitadel",
            type="product",
        ),
        KnowledgeEntity(
            id="temp-org",
            kb_id="kb1",
            version_id="v1",
            name="OpenCitadel",
            normalized_name="opencitadel",
            type="organization",
        ),
    ]
    relations = [
        KnowledgeRelation(
            id="stable-relation",
            kb_id="kb1",
            version_id="v1",
            src_entity_id="temp-product",
            dst_entity_id="temp-org",
            relation="built-by",
            chunk_id="chunk1",
        )
    ]
    refs = [
        KnowledgeEntityRef(
            id="stable-ref",
            kb_id="kb1",
            version_id="v1",
            entity_id="temp-product",
            doc_id="doc1",
        )
    ]
    return entities, relations, refs


@pytest.mark.anyio
async def test_atomic_entity_upsert_uses_exact_identity_and_returning():
    session = _Session(["persisted-product", "persisted-org"])
    repo = _Repo(session)
    await repo.upsert_candidate_graph_batch(
        "kb1", "v1", *_batch()
    )

    entity_sql = [
        sql
        for sql, _params in session.calls
        if "INSERT INTO knowledge_entities" in sql
    ]
    assert len(entity_sql) == 2
    assert all("ON CONFLICT (version_id, normalized_name, type)" in sql for sql in entity_sql)
    assert all("RETURNING knowledge_entities.id" in sql for sql in entity_sql)
    assert all("least(" in sql.lower() for sql in entity_sql)
    assert all("CASE WHEN" in sql for sql in entity_sql)
    assert not any(
        sql.lstrip().startswith("SELECT")
        for sql, _params in session.calls
    )
    relation_sql = next(
        sql
        for sql, _params in session.calls
        if "INSERT INTO knowledge_relations" in sql
    )
    ref_sql = next(
        sql
        for sql, _params in session.calls
        if "INSERT INTO knowledge_entity_refs" in sql
    )
    assert "ON CONFLICT (id) DO NOTHING" in relation_sql
    assert "ON CONFLICT (entity_id, doc_id) DO NOTHING" in ref_sql
    assert session.flushes == 1


@pytest.mark.anyio
async def test_retry_and_same_name_different_type_keep_distinct_identity():
    first = _Session(["persisted-product", "persisted-org"])
    second = _Session(["persisted-product", "persisted-org"])
    await _Repo(first).upsert_candidate_graph_batch(
        "kb1", "v1", *_batch()
    )
    await _Repo(second).upsert_candidate_graph_batch(
        "kb1", "v1", *_batch()
    )

    assert len(
        [
            sql
            for sql, _params in second.calls
            if "INSERT INTO knowledge_entities" in sql
        ]
    ) == 2
    assert not any(
        "DELETE FROM knowledge_" in sql
        for sql, _params in [*first.calls, *second.calls]
    )


@pytest.mark.anyio
async def test_graph_page_sql_is_exact_published_version_keyset_only():
    session = _Session([])
    repo = _Repo(session)
    entities, next_key = await repo.list_entities_page_for_version(
        "kb1",
        "v1",
        q="Open",
        after=("alpha", "e0"),
        limit=10,
    )
    assert entities == []
    assert next_key is None
    sql = session.calls[0][0]
    assert "knowledge_entities.kb_id" in sql
    assert "knowledge_entities.version_id" in sql
    assert "knowledge_base_versions.state IN" in sql
    assert "knowledge_base_versions.published_at IS NOT NULL" in sql
    assert "knowledge_entities.normalized_name >" in sql
    assert "active_version_id" not in sql
    assert "version_id IS NULL" not in sql
