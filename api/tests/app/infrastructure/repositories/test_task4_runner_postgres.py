#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Opt-in real PostgreSQL release gates for KB Task 4 races."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.services.resource_build_service import (
    ResourceBuildService,
)
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.knowledge_base import (
    ChunkLevel,
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
    ResourceKind,
)
from app.domain.models.user import User
from app.domain.services.knowledge_base.ingestion_runner import (
    KBIngestionRunner,
)
from app.infrastructure.models.knowledge_base import (
    KnowledgeBaseModel,
    KnowledgeChunkModel,
)
from app.infrastructure.models.knowledge_version import (
    KnowledgeBaseVersionORM,
)
from app.infrastructure.models.resource_governance import (
    ResourceBuildEventORM,
    ResourceBuildORM,
)
from app.infrastructure.models.user import UserORM
from app.infrastructure.repositories.db_knowledge_base_repository import (
    DBKnowledgeBaseRepository,
)
from app.infrastructure.repositories.db_knowledge_version_repository import (
    DBKnowledgeVersionRepository,
)
from app.infrastructure.repositories.db_resource_governance_repository import (
    DBResourceGovernanceRepository,
)
from app.infrastructure.repositories.db_uow import DBUnitOfWork
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
)
from core.config import get_settings


pytestmark = pytest.mark.skipif(
    os.getenv("OPENCITADEL_RUN_POSTGRES_INTEGRATION") != "1",
    reason=(
        "set OPENCITADEL_RUN_POSTGRES_INTEGRATION=1 for Task 4 "
        "runner/concurrency release gates"
    ),
)


class _NoopNotifier:
    async def publish(self, build_id: str, seq: int) -> None:
        del build_id, seq


async def _seed(
    sessions,
    *,
    suffix: str,
) -> dict[str, str]:
    ids = {
        "user": f"task4-user-{suffix}",
        "kb": f"task4-kb-{suffix}",
        "v1": f"task4-v1-{suffix}",
        "v2": f"task4-v2-{suffix}",
        "build": f"task4-build-{suffix}",
        "doc": f"task4-doc-{suffix}",
        "revision": f"task4-revision-{suffix}",
        "v1_parent": f"task4-v1-parent-{suffix}",
        "v1_child": f"task4-v1-child-{suffix}",
        "v2_parent": f"task4-v2-parent-{suffix}",
        "v2_child": f"task4-v2-child-{suffix}",
    }
    system = AuthorizationContext.system("task4-postgres-seed")
    now = datetime.now(timezone.utc)
    async with sessions() as session:
        await configure_session_authorization(session, system)
        kb_repo = DBKnowledgeBaseRepository(session)
        versions = DBKnowledgeVersionRepository(session)
        builds = DBResourceGovernanceRepository(session)
        session.add(
            UserORM.from_domain(
                User(
                    id=ids["user"],
                    email=f"{suffix}@example.invalid",
                    username=f"task4-{suffix}",
                )
            )
        )
        await kb_repo.save_kb(
            KnowledgeBase(
                id=ids["kb"],
                name="Task 4 PG gate",
                owner_user_id=ids["user"],
            )
        )
        await session.flush()
        await kb_repo.insert_document(
            KnowledgeDocument(
                id=ids["doc"],
                kb_id=ids["kb"],
                title="gate.txt",
            )
        )
        session.add(
            KnowledgeBaseVersionORM(
                id=ids["v1"],
                knowledge_base_id=ids["kb"],
                state=KnowledgeVersionState.READY.value,
                capabilities={"keyword_search": True},
                degraded_reasons=[],
                metrics={"child_chunk_count": 1},
                legacy_snapshot=False,
                created_at=now,
                published_at=now,
            )
        )
        await session.flush()
        await versions.add_revision(
            KnowledgeDocumentRevision(
                id=ids["revision"],
                document_id=ids["doc"],
                source_digest="b" * 64,
                parsed_blocks=[
                    {
                        "page_no": 1,
                        "heading_path": "gate",
                        "text": "candidate searchable text",
                    }
                ],
                page_count=1,
                state=DocumentRevisionState.INDEXED,
            ),
            knowledge_base_id=ids["kb"],
        )
        await versions.add_manifest(
            ids["v1"],
            [
                KnowledgeVersionDocument(
                    version_id=ids["v1"],
                    document_id=ids["doc"],
                    document_revision_id=ids["revision"],
                    ordinal=0,
                    state=DocumentRevisionState.INDEXED,
                )
            ],
            knowledge_base_id=ids["kb"],
        )
        await session.execute(
            update(KnowledgeBaseModel)
            .where(KnowledgeBaseModel.id == ids["kb"])
            .values(active_version_id=ids["v1"])
        )
        await builds.add_build(
            ResourceBuild(
                id=ids["build"],
                resource_kind=ResourceKind.KNOWLEDGE_BASE,
                resource_id=ids["kb"],
                version_id=ids["v2"],
                parent_version_id=ids["v1"],
                command_key=f"task4:{suffix}",
                state=BuildState.RUNNING,
                phase="validate",
                progress=0.88,
                created_by=ids["user"],
            )
        )
        await versions.create_candidate(
            KnowledgeBaseVersion(
                id=ids["v2"],
                knowledge_base_id=ids["kb"],
                parent_version_id=ids["v1"],
                build_id=ids["build"],
            )
        )
        await versions.add_manifest(
            ids["v2"],
            [
                KnowledgeVersionDocument(
                    version_id=ids["v2"],
                    document_id=ids["doc"],
                    document_revision_id=ids["revision"],
                    ordinal=0,
                    state=DocumentRevisionState.INDEXED,
                )
            ],
            knowledge_base_id=ids["kb"],
        )
        await kb_repo.save_chunks(
            [
                KnowledgeChunk(
                    id=ids["v1_parent"],
                    kb_id=ids["kb"],
                    doc_id=ids["doc"],
                    version_id=ids["v1"],
                    level=ChunkLevel.PARENT,
                    content="old active searchable text",
                ),
                KnowledgeChunk(
                    id=ids["v1_child"],
                    kb_id=ids["kb"],
                    doc_id=ids["doc"],
                    version_id=ids["v1"],
                    parent_id=ids["v1_parent"],
                    level=ChunkLevel.CHILD,
                    content="old active searchable text",
                    segmented_content="old active searchable text",
                ),
            ]
        )
        await kb_repo.replace_candidate_chunks(
            ids["kb"],
            ids["v2"],
            [
                KnowledgeChunk(
                    id=ids["v2_parent"],
                    kb_id=ids["kb"],
                    doc_id=ids["doc"],
                    version_id=ids["v2"],
                    level=ChunkLevel.PARENT,
                    content="candidate searchable text",
                ),
                KnowledgeChunk(
                    id=ids["v2_child"],
                    kb_id=ids["kb"],
                    doc_id=ids["doc"],
                    version_id=ids["v2"],
                    parent_id=ids["v2_parent"],
                    level=ChunkLevel.CHILD,
                    content="candidate searchable text",
                    segmented_content="candidate searchable text",
                ),
            ],
        )
        await session.commit()
    return ids


async def _cleanup(sessions, ids: dict[str, str]) -> None:
    system = AuthorizationContext.system("task4-postgres-cleanup")
    async with sessions() as session:
        await configure_session_authorization(session, system)
        await session.execute(
            delete(KnowledgeBaseModel).where(
                KnowledgeBaseModel.id == ids["kb"]
            )
        )
        await session.execute(
            delete(ResourceBuildORM).where(
                ResourceBuildORM.id == ids["build"]
            )
        )
        await session.execute(
            delete(UserORM).where(UserORM.id == ids["user"])
        )
        await session.commit()


@pytest.mark.asyncio
async def test_runner_publish_commit_precedes_terminal_and_active_reads_switch(
    _db_schema,
):
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed(sessions, suffix=uuid.uuid4().hex)
    system = AuthorizationContext.system("task4-publish-terminal")
    try:
        async with sessions() as session:
            await configure_session_authorization(session, system)
            repo = DBKnowledgeBaseRepository(session)
            assert await repo.bm25_search_chunks(
                ids["kb"], "candidate", limit=10
            ) == []
            assert len(
                await repo.bm25_search_chunks(
                    ids["kb"], "old active", limit=10
                )
            ) == 1

        async with DBUnitOfWork(sessions, system) as uow:
            metrics = await uow.knowledge_base.get_candidate_index_metrics(
                ids["kb"], ids["v2"]
            )
            assert await uow.knowledge_version.publish_candidate(
                ids["v2"],
                knowledge_base_id=ids["kb"],
                expected_active_version_id=ids["v1"],
                state=KnowledgeVersionState.DEGRADED,
                capabilities={
                    "keyword_search": True,
                    "vector_search": False,
                    "graph_search": False,
                },
                degraded_reasons=["EMBEDDING_UNAVAILABLE"],
                metrics=metrics,
            )

        async with sessions() as verification:
            await configure_session_authorization(verification, system)
            active = await verification.get(KnowledgeBaseModel, ids["kb"])
            build = await verification.get(ResourceBuildORM, ids["build"])
            events = (
                await verification.execute(
                    select(ResourceBuildEventORM).where(
                        ResourceBuildEventORM.build_id == ids["build"]
                    )
                )
            ).scalars().all()
            assert active.active_version_id == ids["v2"]
            assert build.state == BuildState.RUNNING.value
            assert events == []
            repo = DBKnowledgeBaseRepository(verification)
            assert len(
                await repo.bm25_search_chunks(
                    ids["kb"], "candidate", limit=10
                )
            ) == 1
            assert await repo.bm25_search_chunks(
                ids["kb"], "old active", limit=10
            ) == []

        service = ResourceBuildService(
            uow_factory=lambda: DBUnitOfWork(sessions, system),
            notifier=_NoopNotifier(),
        )
        await service.append_event_authoritative(
            ids["build"],
            phase="publish",
            state=BuildState.DEGRADED,
            progress=1.0,
            payload={
                "capabilities": {
                    "keyword_search": True,
                    "vector_search": False,
                    "graph_search": False,
                },
                "degraded_reasons": ["EMBEDDING_UNAVAILABLE"],
                "metrics": metrics,
            },
            resource_kind=ResourceKind.KNOWLEDGE_BASE,
            resource_id=ids["kb"],
            version_id=ids["v2"],
        )
        async with sessions() as verification:
            await configure_session_authorization(verification, system)
            build = await verification.get(ResourceBuildORM, ids["build"])
            assert build.state == BuildState.DEGRADED.value
            assert build.last_event_seq == 1
    finally:
        await _cleanup(sessions, ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_and_stale_reconcile_race_has_one_terminal(
    _db_schema,
):
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed(sessions, suffix=uuid.uuid4().hex)
    system = AuthorizationContext.system("task4-cancel-reconcile")
    try:
        service = ResourceBuildService(
            uow_factory=lambda: DBUnitOfWork(sessions, system),
            notifier=_NoopNotifier(),
        )
        runner = KBIngestionRunner(
            uow_factory=lambda: DBUnitOfWork(sessions, system),
            file_storage=MagicMock(),
            build_service=service,
        )
        await asyncio.gather(
            runner.cancel(ids["build"]),
            runner.reconcile_stale(ids["build"]),
        )
        async with sessions() as verification:
            await configure_session_authorization(verification, system)
            kb = await verification.get(KnowledgeBaseModel, ids["kb"])
            version = await verification.get(
                KnowledgeBaseVersionORM, ids["v2"]
            )
            build = await verification.get(ResourceBuildORM, ids["build"])
            events = (
                await verification.execute(
                    select(ResourceBuildEventORM).where(
                        ResourceBuildEventORM.build_id == ids["build"]
                    )
                )
            ).scalars().all()
            assert kb.active_version_id == ids["v1"]
            assert version.state == KnowledgeVersionState.FAILED.value
            assert build.state in {
                BuildState.FAILED.value,
                BuildState.CANCELLED.value,
            }
            assert len(events) == 1
    finally:
        await _cleanup(sessions, ids)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("corruption_sql", "error"),
    [
        (
            """
            DELETE FROM knowledge_chunks
            WHERE version_id = :version_id AND level = 'child'
            """,
            "indexed manifest requires child chunks",
        ),
        (
            """
            UPDATE knowledge_base_version_documents
            SET state = 'failed'
            WHERE version_id = :version_id
            """,
            "manifest revision state mismatch",
        ),
        (
            """
            UPDATE knowledge_base_version_documents
            SET state = 'failed'
            WHERE version_id = :version_id;
            UPDATE knowledge_document_revisions
            SET state = 'failed'
            WHERE id = :revision_id
            """,
            "failed manifest cannot own chunks",
        ),
        (
            """
            UPDATE knowledge_chunks
            SET level = 'child'
            WHERE id = :v2_parent
            """,
            "child-parent closure is incomplete",
        ),
        (
            """
            INSERT INTO knowledge_documents
                (id, kb_id, title, source_type, source_ref, mime,
                 page_count, status, created_at, updated_at)
            SELECT :other_doc, :kb_id, 'other', 'upload', '', '', 1,
                   'ready', created_at, updated_at
            FROM knowledge_bases
            WHERE id = :kb_id;
            INSERT INTO knowledge_document_revisions
                (id, document_id, source_ref, source_digest, parsed_blocks,
                 page_count, state, needs_chunk_clone, created_at)
            VALUES
                (:other_revision, :other_doc, '', :other_digest,
                 '[{"text":"other"}]'::jsonb, 1, 'indexed', false,
                 CURRENT_TIMESTAMP);
            INSERT INTO knowledge_base_version_documents
                (version_id, knowledge_base_id, document_id,
                 document_revision_id, ordinal, state)
            VALUES
                (:version_id, :kb_id, :other_doc, :other_revision,
                 1, 'indexed');
            UPDATE knowledge_chunks
            SET doc_id = :other_doc
            WHERE id = :v2_parent
            """,
            "child-parent closure is incomplete",
        ),
        (
            """
            DELETE FROM knowledge_chunks
            WHERE version_id = :version_id;
            UPDATE knowledge_base_version_documents
            SET state = 'failed'
            WHERE version_id = :version_id;
            UPDATE knowledge_document_revisions
            SET state = 'failed'
            WHERE id = :revision_id;
            INSERT INTO knowledge_entities
                (id, kb_id, version_id, name, normalized_name,
                 type, description)
            VALUES
                (:graph_entity, :kb_id, :version_id, 'failed-evidence',
                 :graph_entity, 'concept', '');
            INSERT INTO knowledge_entity_refs
                (id, kb_id, version_id, entity_id, doc_id)
            VALUES
                (:graph_ref, :kb_id, :version_id, :graph_entity,
                 :document_id)
            """,
            "failed manifest cannot own graph evidence",
        ),
    ],
)
async def test_candidate_closure_sql_rejects_corrupt_publish_shape(
    _db_schema,
    corruption_sql,
    error,
):
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed(sessions, suffix=uuid.uuid4().hex)
    system = AuthorizationContext.system("task4-closure-negative")
    try:
        async with sessions() as session:
            await configure_session_authorization(session, system)
            for statement in corruption_sql.split(";"):
                if statement.strip():
                    await session.execute(
                        text(statement),
                        {
                            "version_id": ids["v2"],
                            "revision_id": ids["revision"],
                            "kb_id": ids["kb"],
                            "document_id": ids["doc"],
                            "v2_parent": ids["v2_parent"],
                            "other_doc": f"{ids['doc']}-other",
                            "other_revision": f"{ids['revision']}-other",
                            "other_digest": "c" * 64,
                            "graph_entity": f"{ids['v2']}-failed-entity",
                            "graph_ref": f"{ids['v2']}-failed-ref",
                        },
                    )
            await session.commit()
        async with sessions() as verification:
            await configure_session_authorization(verification, system)
            with pytest.raises(ValueError, match=error):
                await DBKnowledgeBaseRepository(
                    verification
                ).get_candidate_index_metrics(ids["kb"], ids["v2"])
    finally:
        await _cleanup(sessions, ids)
        await engine.dispose()
