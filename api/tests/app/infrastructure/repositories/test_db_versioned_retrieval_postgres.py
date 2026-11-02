"""Canonical PostgreSQL proof for exact historical KB retrieval closure."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import Principal
from app.domain.runtime_policy import KnowledgeRetrievalRunPolicy, initial_execution_policy
from app.domain.services.knowledge_base.retriever import HybridRetriever
from app.domain.services.knowledge_base.zh_tokenizer import (
    segment_for_bm25,
    segment_query_for_bm25,
)
from app.infrastructure.repositories.db_knowledge_base_repository import (
    DBKnowledgeBaseRepository,
)
from app.infrastructure.repositories.db_knowledge_version_repository import (
    DBKnowledgeVersionRepository,
)
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
)
from core.config import load_deployment_settings
from tests.app.execution_test_support import authenticated_session_factory


async def _execute_batch(session, sql: str, params: dict) -> None:
    for statement in sql.split(";"):
        if statement.strip():
            await session.execute(text(statement), params)


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgres_integration")
async def test_historical_retrieval_never_reads_active_candidate_or_foreign_rows():
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    sessions = authenticated_session_factory(
        engine,
        signing_secret=settings.database_authorization_signing_secret,
    )
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
    now = datetime.now(UTC)
    system = AuthorizationContext.system("task5-versioned-retrieval")
    try:
        async with sessions() as session:
            await configure_session_authorization(session, system)
            await _execute_batch(
                session,
                """
                INSERT INTO knowledge_bases
                    (id, name, status, doc_count, chunk_count,
                     vector_degraded, settings,
                     created_at, updated_at)
                VALUES
                    (:kb, 'kb', 'ready', 1, 2, false,
                     '{}'::jsonb, :now, :now),
                    (:foreign_kb, 'foreign', 'ready', 1, 1, false,
                     '{}'::jsonb, :now, :now);
                INSERT INTO knowledge_base_versions
                    (id, knowledge_base_id, state, capabilities,
                     degraded_reasons, metrics, created_at, published_at)
                VALUES
                    (:v1, :kb, 'ready',
                     jsonb_build_object('keyword_search', true),
                     '[]'::jsonb, '{}'::jsonb, :now, :now),
                    (:v2, :kb, 'ready',
                     jsonb_build_object('keyword_search', true),
                     '[]'::jsonb, '{}'::jsonb, :now, :now),
                    (:candidate, :kb, 'building', '{}'::jsonb,
                     '[]'::jsonb, '{}'::jsonb, :now, NULL),
                    (:foreign_v, :foreign_kb, 'ready',
                     jsonb_build_object('keyword_search', true),
                     '[]'::jsonb, '{}'::jsonb, :now, :now);
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
                     parsed_blocks, page_count, state, created_at)
                VALUES
                    (:rev1, :doc, '', :digest1, '[]'::jsonb, 1,
                     'indexed', :now),
                    (:rev2, :doc, '', :digest2, '[]'::jsonb, 1,
                     'indexed', :now),
                    (:candidate_rev, :doc, '', :digest3, '[]'::jsonb, 1,
                     'indexed', :now),
                    (:foreign_rev, :foreign_doc, '', :digest4,
                     '[]'::jsonb, 1, 'indexed', :now);
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
            assert (
                await repository.bm25_search_chunks_for_version(
                    ids["kb"], ids["candidate"], "release", limit=10
                )
                == []
            )
            assert (
                await repository.bm25_search_chunks_for_version(
                    ids["kb"], ids["foreign_v"], "release", limit=10
                )
                == []
            )
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
            document = await repository.get_document_for_version(ids["kb"], ids["v1"], ids["doc"])
            assert document is not None
            assert document[1] == ids["rev1"]
            await session.execute(
                text(
                    "UPDATE knowledge_documents SET updated_at=:later, page_count=9, "
                    "status='failed', warning='new version only' WHERE id=:doc"
                ),
                {"later": now + timedelta(days=1), "doc": ids["doc"]},
            )
            await session.flush()
            historical = await repository.get_document_for_version(ids["kb"], ids["v1"], ids["doc"])
            assert historical is not None
            assert historical[1] == ids["rev1"]
            assert historical[0].updated_at == document[0].updated_at == now
            assert historical[0].page_count == document[0].page_count == 1
            assert historical[0].status == document[0].status == "ready"
            assert historical[0].warning == document[0].warning is None
    finally:
        async with sessions() as cleanup:
            await configure_session_authorization(cleanup, system)
            repository = DBKnowledgeBaseRepository(cleanup)
            await repository.delete_kb(ids["kb"])
            await repository.delete_kb(ids["foreign_kb"])
            await cleanup.commit()
        await engine.dispose()


class _AuthorizedReadUow:
    def __init__(self, sessions, authorization):
        self.sessions = sessions
        self.authorization = authorization

    async def __aenter__(self):
        self.session = self.sessions()
        await self.session.__aenter__()
        await configure_session_authorization(self.session, self.authorization)
        self.knowledge_base = DBKnowledgeBaseRepository(self.session)
        self.knowledge_version = DBKnowledgeVersionRepository(self.session)
        return self

    async def __aexit__(self, *args):
        return await self.session.__aexit__(*args)


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgres_integration")
async def test_keyword_questions_rank_word_coverage_and_keep_version_and_tenant_closure():
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    sessions = authenticated_session_factory(
        engine, signing_secret=settings.database_authorization_signing_secret
    )
    prefix = "keyword-" + uuid.uuid4().hex
    owner, foreign_owner = prefix + "-owner", prefix + "-foreign-owner"
    kb, other_kb, foreign_kb = prefix + "-kb", prefix + "-other-kb", prefix + "-foreign-kb"
    v1, v2, candidate = prefix + "-v1", prefix + "-v2", prefix + "-candidate"
    other_version, foreign_version = prefix + "-other-version", prefix + "-foreign-version"
    doc, failed_doc = prefix + "-doc", prefix + "-failed-doc"
    other_doc, foreign_doc = prefix + "-other-doc", prefix + "-foreign-doc"
    system = AuthorizationContext.system("keyword-retrieval-test")
    authorization = AuthorizationContext.for_principal(Principal(user_id=owner))
    fact = "The Citadel verification beacon is cobalt-17 and rotates every 37 minutes. 验证信标每37分钟轮换。"

    try:
        async with sessions() as session:
            await configure_session_authorization(session, system)
            for user in (owner, foreign_owner):
                await session.execute(
                    text("INSERT INTO users (id, email, username) VALUES (:id, :email, :id)"),
                    {"id": user, "email": user + "@example.test"},
                )
            for knowledge_id, user in ((kb, owner), (other_kb, owner), (foreign_kb, foreign_owner)):
                await session.execute(
                    text(
                        "INSERT INTO knowledge_bases (id, name, status, owner_user_id) VALUES (:id, :id, 'ready', :owner)"
                    ),
                    {"id": knowledge_id, "owner": user},
                )
            for version, knowledge_id, state in (
                (v1, kb, "degraded"),
                (v2, kb, "ready"),
                (candidate, kb, "building"),
                (other_version, other_kb, "ready"),
                (foreign_version, foreign_kb, "ready"),
            ):
                await session.execute(
                    text(
                        "INSERT INTO knowledge_base_versions (id, knowledge_base_id, state, capabilities, degraded_reasons, published_at) "
                        "VALUES (:id, :kb, :state, CAST(:capabilities AS jsonb), "
                        "CAST(:reasons AS jsonb), :published)"
                    ),
                    {
                        "id": version,
                        "kb": knowledge_id,
                        "state": state,
                        "capabilities": '{"keyword_search":true,"vector_search":false,"graph_search":false}',
                        "reasons": '["DOCUMENT_PARTIAL"]' if state == "degraded" else "[]",
                        "published": None if state == "building" else datetime.now(UTC),
                    },
                )
            for knowledge_id, version in (
                (kb, v2),
                (other_kb, other_version),
                (foreign_kb, foreign_version),
            ):
                await session.execute(
                    text("UPDATE knowledge_bases SET active_version_id=:version WHERE id=:kb"),
                    {"kb": knowledge_id, "version": version},
                )
            for document_id, knowledge_id, state in (
                (doc, kb, "ready"),
                (failed_doc, kb, "failed"),
                (other_doc, other_kb, "ready"),
                (foreign_doc, foreign_kb, "ready"),
            ):
                await session.execute(
                    text(
                        "INSERT INTO knowledge_documents (id, kb_id, title, status, mime) VALUES (:id, :kb, :id, :status, 'text/plain')"
                    ),
                    {"id": document_id, "kb": knowledge_id, "status": state},
                )
            for ordinal, (version, knowledge_id, document_id, state) in enumerate(
                (
                    (v1, kb, doc, "indexed"),
                    (v1, kb, failed_doc, "failed"),
                    (v2, kb, doc, "indexed"),
                    (candidate, kb, doc, "indexed"),
                    (other_version, other_kb, other_doc, "indexed"),
                    (foreign_version, foreign_kb, foreign_doc, "indexed"),
                )
            ):
                revision = version + "-revision" + ("-failed" if state == "failed" else "")
                await _execute_batch(
                    session,
                    """
                    INSERT INTO knowledge_document_revisions (id, document_id, source_digest, state, page_count)
                    VALUES (:revision, :doc, :digest, :state, 1);
                    INSERT INTO knowledge_base_version_documents (version_id, knowledge_base_id, document_id, document_revision_id, ordinal, state)
                    VALUES (:version, :kb, :doc, :revision, :ordinal, :state);
                    """,
                    {
                        "revision": revision,
                        "doc": document_id,
                        "digest": str(ordinal + 1) * 64,
                        "state": state,
                        "version": version,
                        "kb": knowledge_id,
                        "ordinal": ordinal,
                    },
                )
            chunks = (
                ("fact", kb, v1, doc, fact),
                ("partial", kb, v1, doc, "Citadel verification"),
                ("repeat", kb, v1, doc, "beacon " * 100),
                ("failed", kb, v1, failed_doc, fact),
                ("active", kb, v2, doc, fact.replace("cobalt-17", "amber-29")),
                ("candidate", kb, candidate, doc, fact),
                ("other", other_kb, other_version, other_doc, fact),
                ("foreign", foreign_kb, foreign_version, foreign_doc, fact),
            )
            for ordinal, (label, knowledge_id, version, document_id, content) in enumerate(chunks):
                await session.execute(
                    text(
                        "INSERT INTO knowledge_chunks (id, kb_id, doc_id, version_id, level, content, content_tsv, page_no, ordinal) "
                        "VALUES (:id, :kb, :doc, :version, 'child', :content, to_tsvector('simple', :segmented), 1, :ordinal)"
                    ),
                    {
                        "id": prefix + "-" + label,
                        "kb": knowledge_id,
                        "doc": document_id,
                        "version": version,
                        "content": content,
                        "segmented": segment_for_bm25(content),
                        "ordinal": ordinal,
                    },
                )
            await session.commit()

        basic = initial_execution_policy().knowledge_base
        vector = Mock(embed=AsyncMock(side_effect=AssertionError("vector is disabled")))
        llm = Mock(invoke=AsyncMock(side_effect=AssertionError("rerank is disabled")))
        retriever = HybridRetriever(
            uow_factory=lambda: _AuthorizedReadUow(sessions, authorization),
            policy=KnowledgeRetrievalRunPolicy(
                vector_enabled=basic.vector_enabled,
                graph_enabled=basic.graphrag.enabled,
                retrieval=basic.retrieval,
                rerank=basic.rerank,
            ),
            vector_service=vector,
            llm=llm,
        )
        for question in (
            "What is the Citadel verification beacon and its rotation interval?",
            "[acceptance:evaluation:citation] What is the Citadel verification beacon and its rotation interval?",
            "Citadel verification beacon",
            "请问验证信标是什么，如何轮换？",
        ):
            response = await retriever.retrieve(kb, v1, question, limit=5)
            assert response.items[0].content == fact
            assert all(
                item.citation.version_id == v1 and item.citation.doc_id == doc
                for item in response.items
            )
            assert all(
                item.citation.document_revision_id == v1 + "-revision" for item in response.items
            )
            assert response.degraded_reasons == ("DOCUMENT_PARTIAL",)
            assert response.capabilities == {
                "keyword_search": True,
                "vector_search": False,
                "graph_search": False,
            }
        async with _AuthorizedReadUow(sessions, authorization) as uow:
            repository = uow.knowledge_base
            ranked = await repository.bm25_search_chunks_for_version(
                kb, v1, "Citadel verification beacon", limit=3
            )
            assert [item.chunk.id for item in ranked] == [
                prefix + "-fact",
                prefix + "-partial",
                prefix + "-repeat",
            ]
            single = await repository.bm25_search_chunks_for_version(kb, v1, "beacon", limit=3)
            assert single[0].chunk.id == prefix + "-repeat"
            quoted = await repository.bm25_search_chunks_for_version(
                kb, v1, "beacon' | !notfound", limit=5
            )
            assert {item.chunk.id for item in quoted} == {prefix + "-fact", prefix + "-repeat"}
            for knowledge_id, version in (
                (kb, candidate),
                (kb, other_version),
                (foreign_kb, foreign_version),
            ):
                assert (
                    await repository.bm25_search_chunks_for_version(
                        knowledge_id, version, "beacon", limit=5
                    )
                    == []
                )
            active = await repository.bm25_search_chunks_for_version(kb, v2, "beacon", limit=5)
            assert [item.chunk.id for item in active] == [prefix + "-active"]
        for noise in ("", "?! | &", "what is the and its", "请问是什么呢"):
            assert segment_query_for_bm25(noise) == ""
            assert (await retriever.retrieve(kb, v1, noise, limit=5)).items == ()
        assert (await retriever.retrieve(kb, v1, "unmatchedword", limit=5)).items == ()
        vector.embed.assert_not_awaited()
        llm.invoke.assert_not_awaited()
    finally:
        async with sessions() as cleanup:
            await configure_session_authorization(cleanup, system)
            repository = DBKnowledgeBaseRepository(cleanup)
            for knowledge_id in (kb, other_kb, foreign_kb):
                await repository.delete_kb(knowledge_id)
            await cleanup.execute(
                text("DELETE FROM users WHERE id IN (:owner, :foreign)"),
                {"owner": owner, "foreign": foreign_owner},
            )
            await cleanup.commit()
        await engine.dispose()
