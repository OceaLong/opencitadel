#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Opt-in PostgreSQL isolation proof for staged KB candidate rows."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from app.domain.models.resource_governance import ResourceBuild, ResourceKind
from app.domain.models.user import User
from app.infrastructure.models.knowledge_base import (
    KnowledgeBaseModel,
    KnowledgeChunkModel,
)
from app.infrastructure.models.resource_governance import ResourceBuildORM
from app.infrastructure.models.knowledge_version import (
    KnowledgeBaseVersionORM,
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
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
)
from core.config import get_settings


@pytest.mark.skipif(
    os.getenv("OPENCITADEL_RUN_POSTGRES_INTEGRATION") != "1",
    reason=(
        "set OPENCITADEL_RUN_POSTGRES_INTEGRATION=1 for PostgreSQL "
        "candidate isolation proof"
    ),
)
@pytest.mark.asyncio
async def test_candidate_chunk_replace_never_touches_active_version_rows(
    _db_schema,
):
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    user_id = f"kb-stage-user-{suffix}"
    kb_id = f"kb-stage-{suffix}"
    old_version_id = f"kb-stage-v1-{suffix}"
    candidate_id = f"kb-stage-v2-{suffix}"
    build_id = f"kb-stage-build-{suffix}"
    document_id = f"kb-stage-doc-{suffix}"
    revision_id = f"kb-stage-rev-{suffix}"
    system = AuthorizationContext.system("kb-stage-isolation-test")
    now = datetime.now(timezone.utc)
    try:
        async with sessions() as session:
            await configure_session_authorization(session, system)
            kb_repo = DBKnowledgeBaseRepository(session)
            versions = DBKnowledgeVersionRepository(session)
            builds = DBResourceGovernanceRepository(session)
            session.add(
                UserORM.from_domain(
                    User(
                        id=user_id,
                        email=f"{suffix}@example.invalid",
                        username=f"kb-stage-{suffix}",
                    )
                )
            )
            await kb_repo.save_kb(
                KnowledgeBase(
                    id=kb_id,
                    name="stage isolation",
                    owner_user_id=user_id,
                )
            )
            await session.flush()
            await kb_repo.insert_document(
                KnowledgeDocument(
                    id=document_id,
                    kb_id=kb_id,
                    title="doc",
                )
            )
            session.add(
                KnowledgeBaseVersionORM(
                    id=old_version_id,
                    knowledge_base_id=kb_id,
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
            revision = KnowledgeDocumentRevision(
                id=revision_id,
                document_id=document_id,
                source_digest="a" * 64,
                parsed_blocks=[
                    {
                        "page_no": 1,
                        "heading_path": "doc",
                        "text": "known",
                    }
                ],
                page_count=1,
                state=DocumentRevisionState.INDEXED,
            )
            await versions.add_revision(
                revision,
                knowledge_base_id=kb_id,
            )
            await versions.add_manifest(
                old_version_id,
                [
                    KnowledgeVersionDocument(
                        version_id=old_version_id,
                        document_id=document_id,
                        document_revision_id=revision_id,
                        ordinal=0,
                        state=DocumentRevisionState.INDEXED,
                    )
                ],
                knowledge_base_id=kb_id,
            )
            await session.execute(
                update(KnowledgeBaseModel)
                .where(KnowledgeBaseModel.id == kb_id)
                .values(active_version_id=old_version_id)
            )
            await builds.add_build(
                ResourceBuild(
                    id=build_id,
                    resource_kind=ResourceKind.KNOWLEDGE_BASE,
                    resource_id=kb_id,
                    version_id=candidate_id,
                    parent_version_id=old_version_id,
                    command_key=f"stage:{suffix}",
                    created_by=user_id,
                )
            )
            await versions.create_candidate(
                KnowledgeBaseVersion(
                    id=candidate_id,
                    knowledge_base_id=kb_id,
                    parent_version_id=old_version_id,
                    build_id=build_id,
                )
            )
            await versions.add_manifest(
                candidate_id,
                [
                    KnowledgeVersionDocument(
                        version_id=candidate_id,
                        document_id=document_id,
                        document_revision_id=revision_id,
                        ordinal=0,
                        state=DocumentRevisionState.INDEXED,
                    )
                ],
                knowledge_base_id=kb_id,
            )
            old_chunk = KnowledgeChunk(
                id=f"old-chunk-{suffix}",
                kb_id=kb_id,
                doc_id=document_id,
                version_id=old_version_id,
                level=ChunkLevel.CHILD,
                content="old active content",
                segmented_content="old active content",
            )
            await kb_repo.save_chunks([old_chunk])
            await kb_repo.replace_candidate_chunks(
                kb_id,
                candidate_id,
                [
                    KnowledgeChunk(
                        id=f"candidate-chunk-{suffix}",
                        kb_id=kb_id,
                        doc_id=document_id,
                        version_id=candidate_id,
                        level=ChunkLevel.CHILD,
                        content="candidate content",
                        segmented_content="candidate content",
                    )
                ],
            )
            await session.commit()

        async with sessions() as verification:
            await configure_session_authorization(verification, system)
            rows = (
                await verification.execute(
                    select(
                        KnowledgeChunkModel.version_id,
                        KnowledgeChunkModel.content,
                    )
                    .where(KnowledgeChunkModel.kb_id == kb_id)
                    .order_by(KnowledgeChunkModel.version_id)
                )
            ).all()
            assert set(rows) == {
                (old_version_id, "old active content"),
                (candidate_id, "candidate content"),
            }
            active = await verification.get(KnowledgeBaseModel, kb_id)
            assert active.active_version_id == old_version_id
    finally:
        async with sessions() as cleanup:
            await configure_session_authorization(cleanup, system)
            await cleanup.execute(
                delete(KnowledgeBaseModel).where(
                    KnowledgeBaseModel.id == kb_id
                )
            )
            await cleanup.execute(
                delete(ResourceBuildORM).where(
                    ResourceBuildORM.id == build_id
                )
            )
            await cleanup.execute(
                delete(UserORM).where(UserORM.id == user_id)
            )
            await cleanup.commit()
        await engine.dispose()
