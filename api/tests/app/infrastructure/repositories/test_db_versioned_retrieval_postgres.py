#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Opt-in PostgreSQL proof for exact historical KB retrieval closure."""
from __future__ import annotations

import os
import uuid
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
        "set OPENCITADEL_RUN_POSTGRES_INTEGRATION=1 for exact "
        "version-scoped retrieval SQL proof"
    ),
)


async def _execute_batch(session, sql: str, params: dict) -> None:
    for statement in sql.split(";"):
        if statement.strip():
            await session.execute(text(statement), params)


@pytest.mark.asyncio
async def test_historical_retrieval_never_reads_active_candidate_or_foreign_rows(
    _db_schema,
):
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    ids = {
        key: f"task5-{key}-{suffix}"
        for key in (
            "kb",
            "foreign_kb",
            "v1",
            "v2",
            "candidate",
            "foreign_v",
            "doc",
            "foreign_doc",
            "rev1",
            "rev2",
            "candidate_rev",
            "foreign_rev",
            "v1_parent",
            "v1_child",
            "v2_parent",
            "v2_child",
            "candidate_child",
            "foreign_child",
        )
    }
    now = datetime.now(timezone.utc)
    system = AuthorizationContext.system("task5-versioned-retrieval")
    try:
        async with sessions() as session:
            await configure_session_authorization(session, system)
            await _execute_batch(
                session,
                """
                INSERT INTO knowledge_bases
                    (id, name, status, doc_count, chunk_count,
                     vector_degraded, legacy_v1_migrated, settings,
                     created_at, updated_at)
                VALUES
                    (:kb, 'kb', 'ready', 1, 2, false, true,
                     '{}'::jsonb, :now, :now),
                    (:foreign_kb, 'foreign', 'ready', 1, 1, false, true,
                     '{}'::jsonb, :now, :now);
                INSERT INTO knowledge_base_versions
                    (id, knowledge_base_id, state, capabilities,
                     degraded_reasons, metrics, legacy_snapshot,
                     created_at, published_at)
                VALUES
                    (:v1, :kb, 'ready',
                     '{"keyword_search":true}'::jsonb,
                     '[]'::jsonb, '{}'::jsonb, false, :now, :now),
                    (:v2, :kb, 'ready',
                     '{"keyword_search":true}'::jsonb,
                     '[]'::jsonb, '{}'::jsonb, false, :now, :now),
                    (:candidate, :kb, 'building', '{}'::jsonb,
                     '[]'::jsonb, '{}'::jsonb, false, :now, NULL),
                    (:foreign_v, :foreign_kb, 'ready',
                     '{"keyword_search":true}'::jsonb,
                     '[]'::jsonb, '{}'::jsonb, false, :now, :now);
                UPDATE knowledge_bases SET active_version_id = :v2
                WHERE id = :kb;
                UPDATE knowledge_bases SET active_version_id = :foreign_v
                WHERE id = :foreign_kb;
                INSERT INTO knowledge_documents
                    (id, kb_id, title, source_type, source_ref, mime,
                     page_count, status, created_at, updated_at)
                VALUES
                    (:doc, :kb, 'doc', 'upload', '', '', 1, 'ready',
                     :now, :now),
                    (:foreign_doc, :foreign_kb, 'foreign', 'upload', '',
                     '', 1, 'ready', :now, :now);
                INSERT INTO knowledge_document_revisions
                    (id, document_id, source_ref, source_digest,
                     parsed_blocks, page_count, state,
                     needs_chunk_clone, created_at)
                VALUES
                    (:rev1, :doc, '', :digest1, '[]'::jsonb, 1,
                     'indexed', false, :now),
                    (:rev2, :doc, '', :digest2, '[]'::jsonb, 1,
                     'indexed', false, :now),
                    (:candidate_rev, :doc, '', :digest3, '[]'::jsonb, 1,
                     'indexed', false, :now),
                    (:foreign_rev, :foreign_doc, '', :digest4,
                     '[]'::jsonb, 1, 'indexed', false, :now);
                INSERT INTO knowledge_base_version_documents
                    (version_id, knowledge_base_id, document_id,
                     document_revision_id, ordinal, state)
                VALUES
                    (:v1, :kb, :doc, :rev1, 0, 'indexed'),
                    (:v2, :kb, :doc, :rev2, 0, 'indexed'),
                    (:candidate, :kb, :doc, :candidate_rev, 0, 'indexed'),
                    (:foreign_v, :foreign_kb, :foreign_doc, :foreign_rev,
                     0, 'indexed');
                INSERT INTO knowledge_chunks
                    (id, kb_id, doc_id, version_id, parent_id, level,
                     content, content_tsv, page_no, heading_path, ordinal)
                VALUES
                    (:v1_parent, :kb, :doc, :v1, NULL, 'parent',
                     'historical parent', to_tsvector('simple', 'release'),
                     1, '', 0),
                    (:v1_child, :kb, :doc, :v1, :v1_parent, 'child',
                     'historical release', to_tsvector('simple', 'release'),
                     1, '', 1),
                    (:v2_parent, :kb, :doc, :v2, NULL, 'parent',
                     'active parent', to_tsvector('simple', 'release'),
                     1, '', 0),
                    (:v2_child, :kb, :doc, :v2, :v2_parent, 'child',
                     'active release', to_tsvector('simple', 'release'),
                     1, '', 1),
                    (:candidate_child, :kb, :doc, :candidate, NULL, 'child',
                     'candidate release',
                     to_tsvector('simple', 'release'), 1, '', 0),
                    (:foreign_child, :foreign_kb, :foreign_doc, :foreign_v,
                     NULL, 'child', 'foreign release',
                     to_tsvector('simple', 'release'), 1, '', 0);
                """,
                {
                    **ids,
                    "now": now,
                    "digest1": "1" * 64,
                    "digest2": "2" * 64,
                    "digest3": "3" * 64,
                    "digest4": "4" * 64,
                },
            )
            await session.commit()

        async with sessions() as session:
            await configure_session_authorization(session, system)
            repository = DBKnowledgeBaseRepository(session)
            v1 = await repository.bm25_search_chunks_for_version(
                ids["kb"], ids["v1"], "release", limit=10
            )
            v2 = await repository.bm25_search_chunks_for_version(
                ids["kb"], ids["v2"], "release", limit=10
            )
            assert [item.chunk.id for item in v1] == [ids["v1_child"]]
            assert [item.document_revision_id for item in v1] == [ids["rev1"]]
            assert [item.chunk.id for item in v2] == [ids["v2_child"]]
            assert await repository.bm25_search_chunks_for_version(
                ids["kb"], ids["candidate"], "release", limit=10
            ) == []
            assert await repository.bm25_search_chunks_for_version(
                ids["kb"], ids["foreign_v"], "release", limit=10
            ) == []
            exact = await repository.get_chunks_by_ids_for_version(
                ids["kb"],
                ids["v1"],
                [ids["v1_child"], ids["v2_child"], ids["foreign_child"]],
            )
            assert [item.chunk.id for item in exact] == [ids["v1_child"]]
            parents = await repository.get_parents_by_ids_for_version(
                ids["kb"],
                ids["v1"],
                [ids["v1_parent"], ids["v2_parent"]],
            )
            assert [item.id for item in parents] == [ids["v1_parent"]]
            document = await repository.get_document_for_version(
                ids["kb"], ids["v1"], ids["doc"]
            )
            assert document is not None
            assert document[1] == ids["rev1"]
    finally:
        async with sessions() as cleanup:
            await configure_session_authorization(cleanup, system)
            await cleanup.execute(
                delete(KnowledgeBaseModel).where(
                    KnowledgeBaseModel.id.in_(
                        [ids["kb"], ids["foreign_kb"]]
                    )
                )
            )
            await cleanup.commit()
        await engine.dispose()
