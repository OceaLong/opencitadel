"""Canonical PostgreSQL gates for graph races and filtered ANN."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.knowledge_base import (
    KnowledgeEntity,
    KnowledgeEntityRef,
    KnowledgeRelation,
)
from app.infrastructure.repositories.db_knowledge_base_repository import (
    DBKnowledgeBaseRepository,
)
from app.infrastructure.repositories.kb._shared import (
    build_versioned_vector_search_statement,
)
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
)
from core.config import load_deployment_settings
from tests.app.execution_test_support import authenticated_session_factory


async def _execute_statements(session, sql: str, params: dict) -> None:
    for statement in sql.split(";"):
        if statement.strip():
            await session.execute(text(statement), params)


async def _seed_graph_candidate(session, ids, now):
    await _execute_statements(
        session,
        """
            INSERT INTO knowledge_bases
                (id, name, status, doc_count, chunk_count,
                 vector_degraded, settings,
                 created_at, updated_at)
            VALUES
                (:kb, 'task6 graph', 'pending', 1, 1, false,
                 '{}'::jsonb, :now, :now);
            INSERT INTO knowledge_base_versions
                (id, knowledge_base_id, state, capabilities,
                 degraded_reasons, metrics, created_at)
            VALUES
                (:version, :kb, 'building', '{}'::jsonb, '[]'::jsonb,
                 '{}'::jsonb, :now);
            INSERT INTO knowledge_documents
                (id, kb_id, title, source_type, source_ref, mime,
                 page_count, status, created_at, updated_at)
            VALUES
                (:doc, :kb, 'doc', 'upload', '', 'text/plain',
                 1, 'ready', :now, :now);
            INSERT INTO knowledge_document_revisions
                (id, document_id, source_ref, source_digest,
                 parsed_blocks, page_count, state, created_at)
            VALUES
                (:revision, :doc, '', :digest, '[]'::jsonb, 1,
                 'indexed', :now);
            INSERT INTO knowledge_base_version_documents
                (version_id, knowledge_base_id, document_id,
                 document_revision_id, ordinal, state)
            VALUES
                (:version, :kb, :doc, :revision, 0, 'indexed');
            INSERT INTO knowledge_chunks
                (id, kb_id, doc_id, version_id, level, content,
                 content_tsv, ordinal)
            VALUES
                (:chunk, :kb, :doc, :version, 'parent', 'graph source',
                 to_tsvector('simple', 'graph source'), 0);
            """,
        {**ids, "digest": "a" * 64, "now": now},
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgres_integration")
async def test_concurrent_graph_upsert_and_retry_create_one_identity():
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    sessions = authenticated_session_factory(
        engine,
        signing_secret=settings.session_secret,
    )
    suffix = uuid.uuid4().hex
    ids = {key: f"task6-{key}-{suffix}" for key in ("kb", "version", "doc", "revision", "chunk")}
    system = AuthorizationContext.system("task6-graph-race")
    now = datetime.now(UTC)
    try:
        async with sessions() as session:
            await configure_session_authorization(session, system)
            await _seed_graph_candidate(session, ids, now)
            await session.commit()

        async def write_batch(
            temp_suffix: str,
            *,
            display_name: str,
            description: str,
        ):
            async with sessions() as session:
                await configure_session_authorization(session, system)
                repo = DBKnowledgeBaseRepository(session)
                product = KnowledgeEntity(
                    id=f"temp-product-{suffix}-{temp_suffix}",
                    kb_id=ids["kb"],
                    version_id=ids["version"],
                    name=display_name,
                    normalized_name="opencitadel",
                    type="product",
                    description=description,
                )
                organization = KnowledgeEntity(
                    id=f"temp-org-{suffix}-{temp_suffix}",
                    kb_id=ids["kb"],
                    version_id=ids["version"],
                    name="OpenCitadel",
                    normalized_name="opencitadel",
                    type="organization",
                )
                await repo.upsert_candidate_graph_batch(
                    ids["kb"],
                    ids["version"],
                    [product, organization],
                    [
                        KnowledgeRelation(
                            id=f"stable-relation-{suffix}",
                            kb_id=ids["kb"],
                            version_id=ids["version"],
                            src_entity_id=product.id,
                            dst_entity_id=organization.id,
                            relation="built-by",
                            chunk_id=ids["chunk"],
                        )
                    ],
                    [
                        KnowledgeEntityRef(
                            id=f"stable-ref-{suffix}",
                            kb_id=ids["kb"],
                            version_id=ids["version"],
                            entity_id=product.id,
                            doc_id=ids["doc"],
                        )
                    ],
                )
                await session.commit()

        await __import__("asyncio").gather(
            write_batch(
                "a",
                display_name="opencitadel",
                description="short",
            ),
            write_batch(
                "b",
                display_name="OpenCitadel",
                description="a much longer deterministic description",
            ),
        )
        # A third identical delivery proves retry idempotency separately
        # from the concurrent conflict.
        await write_batch(
            "retry",
            display_name="OpenCitadel",
            description="medium description",
        )

        async with sessions() as session:
            await configure_session_authorization(session, system)
            repo = DBKnowledgeBaseRepository(session)

            async def merge_one(
                entity_type: str,
                entity_id: str,
                display_name: str,
                description: str,
            ):
                await repo.upsert_candidate_graph_batch(
                    ids["kb"],
                    ids["version"],
                    [
                        KnowledgeEntity(
                            id=entity_id,
                            kb_id=ids["kb"],
                            version_id=ids["version"],
                            name=display_name,
                            normalized_name="merge name",
                            type=entity_type,
                            description=description,
                        )
                    ],
                    [],
                    [],
                )

            await merge_one(
                "concept-forward",
                f"merge-forward-a-{suffix}",
                "merge name",
                "short",
            )
            await merge_one(
                "concept-forward",
                f"merge-forward-b-{suffix}",
                "Merge Name",
                "the longest deterministic description",
            )
            await merge_one(
                "concept-reverse",
                f"merge-reverse-b-{suffix}",
                "Merge Name",
                "the longest deterministic description",
            )
            await merge_one(
                "concept-reverse",
                f"merge-reverse-a-{suffix}",
                "merge name",
                "short",
            )
            await session.commit()

        async with sessions() as session:
            await configure_session_authorization(session, system)
            counts = (
                await session.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM knowledge_entities
                           WHERE version_id = :version) AS entities,
                          (SELECT count(*) FROM knowledge_relations
                           WHERE version_id = :version) AS relations,
                          (SELECT count(*) FROM knowledge_entity_refs
                           WHERE version_id = :version) AS refs
                        """
                    ),
                    {"version": ids["version"]},
                )
            ).one()
            assert tuple(counts) == (4, 1, 1)
            merged = (
                await session.execute(
                    text(
                        """
                        SELECT name, description
                        FROM knowledge_entities
                        WHERE version_id = :version
                          AND normalized_name = 'opencitadel'
                          AND type = 'product'
                        """
                    ),
                    {"version": ids["version"]},
                )
            ).one()
            assert merged.name in {"opencitadel", "OpenCitadel"}
            assert merged.description == "a much longer deterministic description"
            forward_reverse = (
                await session.execute(
                    text(
                        """
                        SELECT type, name, description
                        FROM knowledge_entities
                        WHERE version_id = :version
                          AND normalized_name = 'merge name'
                          AND type IN (
                            'concept-forward', 'concept-reverse'
                          )
                        ORDER BY type
                        """
                    ),
                    {"version": ids["version"]},
                )
            ).all()
            assert len(forward_reverse) == 2
            assert forward_reverse[0][1:] == forward_reverse[1][1:]
    finally:
        async with sessions() as session:
            await configure_session_authorization(session, system)
            await DBKnowledgeBaseRepository(session).delete_kb(ids["kb"])
            await session.commit()
        await engine.dispose()


def _vector(rank: float) -> str:
    values = [1.0, rank, *([0.0] * 1534)]
    return "[" + ",".join(str(value) for value in values) + "]"


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgres_integration")
async def test_filtered_hnsw_iterative_scan_recall_and_explain_gate(
    capsys,
):
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    sessions = authenticated_session_factory(
        engine,
        signing_secret=settings.session_secret,
    )
    suffix = uuid.uuid4().hex
    kb = f"task6-ann-kb-{suffix}"
    foreign_kb = f"task6-ann-foreign-kb-{suffix}"
    version = f"task6-ann-v1-{suffix}"
    other_version = f"task6-ann-v2-{suffix}"
    candidate = f"task6-ann-candidate-{suffix}"
    foreign_version = f"task6-ann-foreign-v1-{suffix}"
    doc = f"task6-ann-doc-{suffix}"
    foreign_doc = f"task6-ann-foreign-doc-{suffix}"
    now = datetime.now(UTC)
    system = AuthorizationContext.system("task6-filtered-ann")
    query = _vector(0.0)
    try:
        async with sessions() as session:
            await configure_session_authorization(session, system)
            await _execute_statements(
                session,
                """
                    INSERT INTO knowledge_bases
                        (id, name, status, doc_count, chunk_count,
                         vector_degraded, settings,
                         created_at, updated_at)
                    VALUES (:kb, 'ann', 'ready', 1, 360, false,
                            '{}'::jsonb, :now, :now);
                    INSERT INTO knowledge_bases
                        (id, name, status, doc_count, chunk_count,
                         vector_degraded, settings,
                         created_at, updated_at)
                    VALUES (:foreign_kb, 'foreign ann', 'ready', 1, 120,
                            false, '{}'::jsonb, :now, :now);
                    INSERT INTO knowledge_documents
                        (id, kb_id, title, source_type, source_ref, mime,
                         page_count, status, created_at, updated_at)
                    VALUES (:doc, :kb, 'ann doc', 'upload', '',
                            'text/plain', 1, 'ready', :now, :now);
                    INSERT INTO knowledge_documents
                        (id, kb_id, title, source_type, source_ref, mime,
                         page_count, status, created_at, updated_at)
                    VALUES (:foreign_doc, :foreign_kb, 'foreign ann doc',
                            'upload', '', 'text/plain', 1, 'ready',
                            :now, :now);
                    """,
                {
                    "kb": kb,
                    "foreign_kb": foreign_kb,
                    "doc": doc,
                    "foreign_doc": foreign_doc,
                    "now": now,
                },
            )
            for index, (
                row_kb,
                row_doc,
                version_id,
                state,
                published,
                distance_offset,
            ) in enumerate(
                (
                    (kb, doc, version, "ready", now, 1.0),
                    (kb, doc, other_version, "ready", now, 0.2),
                    (kb, doc, candidate, "building", None, 0.1),
                    (
                        foreign_kb,
                        foreign_doc,
                        foreign_version,
                        "ready",
                        now,
                        0.05,
                    ),
                )
            ):
                revision = f"task6-ann-rev-{index}-{suffix}"
                await _execute_statements(
                    session,
                    """
                        INSERT INTO knowledge_base_versions
                            (id, knowledge_base_id, state, capabilities,
                             degraded_reasons, metrics, created_at,
                             published_at)
                        VALUES (:version, :kb, :state,
                                jsonb_build_object('vector_search', true),
                                '[]'::jsonb, '{}'::jsonb, :now,
                                :published);
                        INSERT INTO knowledge_document_revisions
                            (id, document_id, source_ref, source_digest,
                             parsed_blocks, page_count, state, created_at)
                        VALUES (:revision, :doc, '', :digest,
                                '[]'::jsonb, 1, 'indexed', :now);
                        INSERT INTO knowledge_base_version_documents
                            (version_id, knowledge_base_id, document_id,
                             document_revision_id, ordinal, state)
                        VALUES (:version, :kb, :doc, :revision, 0,
                                'indexed');
                        """,
                    {
                        "version": version_id,
                        "kb": row_kb,
                        "state": state,
                        "now": now,
                        "published": published,
                        "revision": revision,
                        "doc": row_doc,
                        "digest": f"{index + 1:064x}",
                    },
                )
                rows = [
                    {
                        "id": f"task6-ann-{index}-{rank}-{suffix}",
                        "kb": row_kb,
                        "doc": row_doc,
                        "version": version_id,
                        "content": f"rank {rank}",
                        "ordinal": rank,
                        "embedding": _vector((rank + distance_offset) * 0.001),
                    }
                    for rank in range(120)
                ]
                await session.execute(
                    text(
                        """
                        INSERT INTO knowledge_chunks
                            (id, kb_id, doc_id, version_id, level,
                             content, content_tsv, ordinal, embedding)
                        VALUES
                            (:id, :kb, :doc, :version, 'child',
                             :content, to_tsvector('simple', :content),
                             :ordinal, CAST(:embedding AS vector))
                        """
                    ),
                    rows,
                )
            await session.commit()

        async with sessions() as session:
            await configure_session_authorization(session, system)
            params = {
                "kb_id": kb,
                "version_id": version,
                "query": query,
                "limit": 10,
            }
            await session.execute(text("SET LOCAL enable_indexscan = off"))
            await session.execute(text("SET LOCAL enable_bitmapscan = off"))
            exact_started = time.perf_counter()
            exact_ids = list(
                (
                    await session.execute(
                        build_versioned_vector_search_statement(),
                        params,
                    )
                ).scalars()
            )
            exact_latency = time.perf_counter() - exact_started

            await session.execute(text("SET LOCAL enable_indexscan = on"))
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            await session.execute(text("SET LOCAL enable_sort = off"))
            repo = DBKnowledgeBaseRepository(session)
            ann_started = time.perf_counter()
            try:
                ann_rows = await repo.vector_search_chunks_for_version(
                    kb,
                    version,
                    [1.0, 0.0, *([0.0] * 1534)],
                    limit=10,
                )
            except RuntimeError as exc:
                pytest.fail(str(exc))
            ann_latency = time.perf_counter() - ann_started
            ann_ids = [row.chunk.id for row in ann_rows]
            active = (await session.execute(text("SHOW hnsw.iterative_scan"))).scalar_one()
            assert active == "strict_order"
            plan = (
                await session.execute(
                    build_versioned_vector_search_statement(explain=True),
                    params,
                )
            ).scalar_one()
            plan_text = json.dumps(plan)
            recall = len(set(exact_ids) & set(ann_ids)) / 10
            print(
                f"filtered ANN recall@10={recall:.3f} "
                f"ann={ann_latency:.6f}s exact={exact_latency:.6f}s"
            )
            assert recall >= 0.95
            assert "ix_kb_chunks_embedding" in plan_text, plan_text
            assert kb in plan_text
            assert "version_id" in plan_text
            assert "knowledge_base_versions" in plan_text
            assert "knowledge_base_version_documents" in plan_text
            assert "knowledge_document_revisions" in plan_text
            assert set(ann_ids).issubset({f"task6-ann-0-{rank}-{suffix}" for rank in range(120)})
            assert ann_latency <= exact_latency * 20 + 1.0
            assert "recall@10" in capsys.readouterr().out
    finally:
        async with sessions() as session:
            await configure_session_authorization(session, system)
            repository = DBKnowledgeBaseRepository(session)
            await repository.delete_kb(kb)
            await repository.delete_kb(foreign_kb)
            await session.commit()
        await engine.dispose()
