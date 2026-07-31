#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Opt-in PostgreSQL release gates for Task 4 fix round 2."""
from __future__ import annotations

import os
import uuid
import hashlib
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.models.knowledge_base import KnowledgeBaseModel
from app.infrastructure.repositories.db_knowledge_base_repository import (
    DBKnowledgeBaseRepository,
)
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
)
from core.config import get_settings


pytestmark = pytest.mark.skipif(
    os.getenv("OPENCITADEL_RUN_POSTGRES_INTEGRATION") != "1",
    reason=(
        "set OPENCITADEL_RUN_POSTGRES_INTEGRATION=1 for Task 4 fix2 "
        "legacy compatibility and clone release gates"
    ),
)


async def _execute_batch(session, sql: str, params: dict) -> None:
    for statement in sql.split(";"):
        if statement.strip():
            await session.execute(text(statement), params)


@pytest.mark.asyncio
async def test_postgres_legacy_null_compatibility_pre_post_cas_and_loser_matrix(
    _db_schema,
):
    """NULL old-writer rows are visible only while legacy v1 is active."""
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    ids = {
        key: f"task4-fix2-{key}-{suffix}"
        for key in (
            "kb",
            "v1",
            "v2",
            "v3",
            "old_doc",
            "candidate_doc",
            "revision",
            "null_parent",
            "null_child",
            "candidate_parent",
            "candidate_child",
            "null_entity_a",
            "null_entity_b",
            "candidate_entity_a",
            "candidate_entity_b",
            "null_relation_seed",
            "null_relation_related",
            "candidate_relation",
            "null_ref",
            "candidate_ref",
        )
    }
    system = AuthorizationContext.system("task4-fix2-null-compat")
    now = datetime.now(timezone.utc)
    vector_literal = "[" + ",".join(["0"] * 1536) + "]"
    try:
        async with sessions() as session:
            await configure_session_authorization(session, system)
            await _execute_batch(
                session,
                """
                    INSERT INTO knowledge_bases
                        (id, name, status, doc_count, chunk_count,
                         vector_degraded, legacy_v1_migrated,
                         settings, created_at, updated_at)
                    VALUES
                        (:kb, 'compat', 'ready', 1, 1, false, true,
                         '{}'::jsonb, :now, :now);
                    INSERT INTO knowledge_base_versions
                        (id, knowledge_base_id, state, capabilities,
                         degraded_reasons, metrics, legacy_snapshot,
                         created_at, published_at)
                    VALUES
                        (:v1, :kb, 'ready',
                         '{"keyword_search":true,"vector_search":true,'
                         '"graph_search":true}'::jsonb,
                         '[]'::jsonb, '{}'::jsonb, true, :now, :now),
                        (:v2, :kb, 'ready',
                         '{"keyword_search":true,"vector_search":true,'
                         '"graph_search":true}'::jsonb,
                         '[]'::jsonb, '{}'::jsonb, false, :now, :now),
                        (:v3, :kb, 'building', '{}'::jsonb,
                         '[]'::jsonb, '{}'::jsonb, false, :now, NULL);
                    UPDATE knowledge_bases
                    SET active_version_id = :v1
                    WHERE id = :kb;
                    INSERT INTO knowledge_documents
                        (id, kb_id, title, source_type, source_ref, mime,
                         page_count, status, created_at, updated_at)
                    VALUES
                        (:old_doc, :kb, 'old', 'upload', '', '', 1,
                         'ready', :now, :now),
                        (:candidate_doc, :kb, 'candidate', 'upload', '', '',
                         1, 'ready', :now, :now);
                    INSERT INTO knowledge_document_revisions
                        (id, document_id, source_ref, source_digest,
                         parsed_blocks, page_count, state,
                         needs_chunk_clone, created_at)
                    VALUES
                        (:revision, :candidate_doc, '', :digest,
                         '[{"text":"candidate"}]'::jsonb, 1, 'indexed',
                         false, :now);
                    INSERT INTO knowledge_base_version_documents
                        (version_id, knowledge_base_id, document_id,
                         document_revision_id, ordinal, state)
                    VALUES
                        (:v2, :kb, :candidate_doc, :revision, 0, 'indexed');
                    """,
                {
                    **ids,
                    "now": now,
                    "digest": "a" * 64,
                },
            )
            await _execute_batch(
                session,
                """
                    INSERT INTO knowledge_chunks
                        (id, kb_id, doc_id, version_id, parent_id, level,
                         content, content_tsv, page_no, heading_path,
                         ordinal, embedding)
                    VALUES
                        (:null_parent, :kb, :old_doc, NULL, NULL, 'parent',
                         'legacy parent', to_tsvector('simple', 'legacy'),
                         1, 'legacy', 0, :vector::vector),
                        (:null_child, :kb, :old_doc, NULL, :null_parent,
                         'child', 'legacy searchable',
                         to_tsvector('simple', 'legacy searchable'),
                         1, 'legacy', 1, :vector::vector),
                        (:candidate_parent, :kb, :candidate_doc, :v2, NULL,
                         'parent', 'candidate parent',
                         to_tsvector('simple', 'candidate'), 1,
                         'candidate', 0, :vector::vector),
                        (:candidate_child, :kb, :candidate_doc, :v2,
                         :candidate_parent, 'child', 'candidate searchable',
                         to_tsvector('simple', 'candidate searchable'), 1,
                         'candidate', 1, :vector::vector);
                    INSERT INTO knowledge_entities
                        (id, kb_id, version_id, name, normalized_name,
                         type, description)
                    VALUES
                        (:null_entity_a, :kb, NULL, 'legacy-a',
                         'legacy-a', 'concept', ''),
                        (:null_entity_b, :kb, NULL, 'legacy-b',
                         'legacy-b', 'concept', ''),
                        (:candidate_entity_a, :kb, :v2, 'candidate-a',
                         'candidate-a', 'concept', ''),
                        (:candidate_entity_b, :kb, :v2, 'candidate-b',
                         'candidate-b', 'concept', '');
                    INSERT INTO knowledge_relations
                        (id, kb_id, version_id, src_entity_id,
                         dst_entity_id, relation, chunk_id)
                    VALUES
                        (:null_relation_seed, :kb, NULL, :null_entity_a,
                         :null_entity_b, 'legacy-seed', :null_child),
                        (:null_relation_related, :kb, NULL, :null_entity_a,
                         :null_entity_b, 'legacy-related', :null_parent),
                        (:candidate_relation, :kb, :v2,
                         :candidate_entity_a, :candidate_entity_b,
                         'candidate', :candidate_child);
                    INSERT INTO knowledge_entity_refs
                        (id, kb_id, version_id, entity_id, doc_id)
                    VALUES
                        (:null_ref, :kb, NULL, :null_entity_a, :old_doc),
                        (:candidate_ref, :kb, :v2, :candidate_entity_a,
                         :candidate_doc);
                    """,
                {**ids, "vector": vector_literal},
            )
            await session.commit()

        async with sessions() as before:
            await configure_session_authorization(before, system)
            repository = DBKnowledgeBaseRepository(before)
            assert {
                item.id for item in await repository.list_documents(ids["kb"])
            } == {ids["old_doc"]}
            assert await repository.count_documents(ids["kb"]) == 1
            assert (
                await repository.count_ready_documents([ids["kb"]])
            )[ids["kb"]] == 1
            assert await repository.count_child_chunks(ids["kb"]) == 1
            assert {
                item[0].id
                for item in await repository.bm25_search_chunks(
                    ids["kb"], "legacy", limit=10
                )
            } == {ids["null_child"]}
            assert {
                item[0].id
                for item in await repository.vector_search_chunks(
                    ids["kb"], [0.0] * 1536, limit=10
                )
            } == {ids["null_child"]}
            assert {
                item.id
                for item in await repository.get_parents_by_ids(
                    [ids["null_parent"], ids["candidate_parent"]]
                )
            } == {ids["null_parent"]}
            assert {
                item.id
                for item in await repository.list_entities(ids["kb"])
            } == {ids["null_entity_a"], ids["null_entity_b"]}
            assert {
                item.id
                for item in await repository.list_relations_for_entities(
                    ids["kb"],
                    [ids["null_entity_a"], ids["candidate_entity_a"]],
                )
            } == {
                ids["null_relation_seed"],
                ids["null_relation_related"],
            }
            assert await repository.get_related_chunk_ids(
                ids["kb"], [ids["null_child"]], limit=10
            ) == [ids["null_parent"]]

        async with sessions() as switch:
            await configure_session_authorization(switch, system)
            result = await switch.execute(
                text(
                    """
                    UPDATE knowledge_bases
                    SET active_version_id = :v2
                    WHERE id = :kb
                      AND active_version_id = :v1
                    """
                ),
                ids,
            )
            assert result.rowcount == 1
            loser = await switch.execute(
                text(
                    """
                    UPDATE knowledge_bases
                    SET active_version_id = :v3
                    WHERE id = :kb
                      AND active_version_id = :v1
                    """
                ),
                ids,
            )
            assert loser.rowcount == 0
            await switch.commit()

        async with sessions() as after:
            await configure_session_authorization(after, system)
            repository = DBKnowledgeBaseRepository(after)
            assert {
                item.id for item in await repository.list_documents(ids["kb"])
            } == {ids["candidate_doc"]}
            assert await repository.count_child_chunks(ids["kb"]) == 1
            assert await repository.bm25_search_chunks(
                ids["kb"], "legacy", limit=10
            ) == []
            assert {
                item[0].id
                for item in await repository.bm25_search_chunks(
                    ids["kb"], "candidate", limit=10
                )
            } == {ids["candidate_child"]}
            assert {
                item.id
                for item in await repository.list_entities(ids["kb"])
            } == {
                ids["candidate_entity_a"],
                ids["candidate_entity_b"],
            }
            active = await after.get(KnowledgeBaseModel, ids["kb"])
            assert active.active_version_id == ids["v2"]
    finally:
        async with sessions() as cleanup:
            await configure_session_authorization(cleanup, system)
            await cleanup.execute(
                delete(KnowledgeBaseModel).where(
                    KnowledgeBaseModel.id == ids["kb"]
                )
            )
            await cleanup.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_c7_revision_clones_v1_to_v2_to_v3_byte_stably(
    _db_schema,
):
    """The immutable c7 marker and exact tsvector survive two generations."""
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    kb_id = f"task4-fix2-clone-{suffix}"
    document_id = f"{kb_id}-doc"
    v1_id = hashlib.md5(
        f"knowledge-base-version:v1:{kb_id}".encode()
    ).hexdigest()
    revision_id = hashlib.md5(
        f"knowledge-document-revision:v1:{document_id}".encode()
    ).hexdigest()
    v2_id = f"{kb_id}-v2"
    v3_id = f"{kb_id}-v3"
    source_parent = f"{kb_id}-parent"
    source_child = f"{kb_id}-child"
    now = datetime.now(timezone.utc)
    vector_literal = "[" + ",".join(["0"] * 1536) + "]"
    system = AuthorizationContext.system("task4-fix2-c7-clone")
    v1_before = None
    try:
        async with sessions() as session:
            await configure_session_authorization(session, system)
            await _execute_batch(
                session,
                """
                INSERT INTO knowledge_bases
                    (id, name, status, doc_count, chunk_count,
                     vector_degraded, legacy_v1_migrated,
                     settings, created_at, updated_at)
                VALUES
                    (:kb, 'clone', 'ready', 1, 1, false, true,
                     '{}'::jsonb, :now, :now);
                INSERT INTO knowledge_base_versions
                    (id, knowledge_base_id, state, capabilities,
                     degraded_reasons, metrics, legacy_snapshot,
                     created_at, published_at)
                VALUES
                    (:v1, :kb, 'ready',
                     '{"keyword_search":true,"vector_search":true}'::jsonb,
                     '[]'::jsonb, '{}'::jsonb, true, :now, :now),
                    (:v2, :kb, 'building', '{}'::jsonb,
                     '[]'::jsonb, '{}'::jsonb, false, :now, NULL);
                UPDATE knowledge_bases
                SET active_version_id = :v1
                WHERE id = :kb;
                INSERT INTO knowledge_documents
                    (id, kb_id, title, source_type, source_ref, mime,
                     page_count, status, created_at, updated_at)
                VALUES
                    (:doc, :kb, 'legacy', 'upload', '', '', 9,
                     'ready', :now, :now);
                INSERT INTO knowledge_document_revisions
                    (id, document_id, source_ref, source_digest,
                     parsed_blocks, page_count, state,
                     needs_chunk_clone, created_at)
                VALUES
                    (:revision, :doc, '', :digest, '[]'::jsonb, 9,
                     'indexed', true, :now);
                INSERT INTO knowledge_base_version_documents
                    (version_id, knowledge_base_id, document_id,
                     document_revision_id, ordinal, state)
                VALUES
                    (:v1, :kb, :doc, :revision, 0, 'indexed'),
                    (:v2, :kb, :doc, :revision, 0, 'indexed');
                INSERT INTO knowledge_chunks
                    (id, kb_id, doc_id, version_id, parent_id, level,
                     content, content_tsv, page_no, heading_path,
                     ordinal, embedding)
                VALUES
                    (:source_parent, :kb, :doc, :v1, NULL, 'parent',
                     '父块 原文', '''父块'':1 ''原文'':4'::tsvector,
                     7, '章/节', 11, :vector::vector),
                    (:source_child, :kb, :doc, :v1, :source_parent,
                     'child', '子块 原文',
                     '''子块'':2 ''原文'':9'::tsvector,
                     8, '章/节/子', 12, :vector::vector);
                """,
                {
                    "kb": kb_id,
                    "v1": v1_id,
                    "v2": v2_id,
                    "doc": document_id,
                    "revision": revision_id,
                    "digest": "d" * 64,
                    "source_parent": source_parent,
                    "source_child": source_child,
                    "vector": vector_literal,
                    "now": now,
                },
            )
            v1_before = (
                await session.execute(
                    text(
                        """
                        SELECT id, parent_id, level, content,
                               content_tsv::text, embedding::text,
                               page_no, heading_path, ordinal
                        FROM knowledge_chunks
                        WHERE version_id = :v1
                        ORDER BY id
                        """
                    ),
                    {"v1": v1_id},
                )
            ).all()
            repository = DBKnowledgeBaseRepository(session)
            v2_chunks = await repository.clone_version_chunks(
                kb_id, v1_id, v2_id, [document_id]
            )
            await repository.replace_candidate_chunks(
                kb_id, v2_id, v2_chunks
            )
            await session.execute(
                text(
                    """
                    UPDATE knowledge_base_versions
                    SET state = 'ready', published_at = :now
                    WHERE id = :v2;
                    """
                ),
                {"v2": v2_id, "now": now},
            )
            await session.execute(
                text(
                    """
                    UPDATE knowledge_bases
                    SET active_version_id = :v2
                    WHERE id = :kb
                    """
                ),
                {"kb": kb_id, "v2": v2_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO knowledge_base_versions
                        (id, knowledge_base_id, parent_version_id, state,
                         capabilities, degraded_reasons, metrics,
                         legacy_snapshot, created_at)
                    VALUES
                        (:v3, :kb, :v2, 'building', '{}'::jsonb,
                         '[]'::jsonb, '{}'::jsonb, false, :now);
                    """
                ),
                {"kb": kb_id, "v2": v2_id, "v3": v3_id, "now": now},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO knowledge_base_version_documents
                        (version_id, knowledge_base_id, document_id,
                         document_revision_id, ordinal, state)
                    VALUES
                        (:v3, :kb, :doc, :revision, 0, 'indexed')
                    """
                ),
                {
                    "kb": kb_id,
                    "v3": v3_id,
                    "doc": document_id,
                    "revision": revision_id,
                },
            )
            v3_chunks = await repository.clone_version_chunks(
                kb_id, v2_id, v3_id, [document_id]
            )
            first_ids = [chunk.id for chunk in v3_chunks]
            await repository.replace_candidate_chunks(
                kb_id, v3_id, v3_chunks
            )
            retried = await repository.clone_version_chunks(
                kb_id, v2_id, v3_id, [document_id]
            )
            assert [chunk.id for chunk in retried] == first_ids
            await repository.replace_candidate_chunks(
                kb_id, v3_id, retried
            )
            await session.execute(
                text(
                    """
                    UPDATE knowledge_base_versions
                    SET state = 'ready', published_at = :now
                    WHERE id = :v3
                    """
                ),
                {"v3": v3_id, "now": now},
            )
            await session.execute(
                text(
                    """
                    UPDATE knowledge_bases
                    SET active_version_id = :v3
                    WHERE id = :kb
                    """
                ),
                {"kb": kb_id, "v3": v3_id},
            )
            await session.commit()

        async with sessions() as verification:
            await configure_session_authorization(verification, system)
            v1_after = (
                await verification.execute(
                    text(
                        """
                        SELECT id, parent_id, level, content,
                               content_tsv::text, embedding::text,
                               page_no, heading_path, ordinal
                        FROM knowledge_chunks
                        WHERE version_id = :v1
                        ORDER BY id
                        """
                    ),
                    {"v1": v1_id},
                )
            ).all()
            assert v1_after == v1_before
            vectors = (
                await verification.execute(
                    text(
                        """
                        SELECT version_id, level, content, content_tsv::text,
                               embedding::text, page_no, heading_path, ordinal
                        FROM knowledge_chunks
                        WHERE version_id IN (:v1, :v2, :v3)
                        ORDER BY version_id, level, ordinal
                        """
                    ),
                    {"v1": v1_id, "v2": v2_id, "v3": v3_id},
                )
            ).all()
            by_version = {
                version_id: [
                    tuple(row[1:])
                    for row in vectors
                    if row[0] == version_id
                ]
                for version_id in (v1_id, v2_id, v3_id)
            }
            assert by_version[v1_id] == by_version[v2_id]
            assert by_version[v2_id] == by_version[v3_id]
            repository = DBKnowledgeBaseRepository(verification)
            matches = await repository.bm25_search_chunks(
                kb_id, "子块 原文", limit=10
            )
            assert len(matches) == 1
            assert matches[0][0].version_id == v3_id
    finally:
        async with sessions() as cleanup:
            await configure_session_authorization(cleanup, system)
            await cleanup.execute(
                delete(KnowledgeBaseModel).where(
                    KnowledgeBaseModel.id == kb_id
                )
            )
            await cleanup.commit()
        await engine.dispose()
