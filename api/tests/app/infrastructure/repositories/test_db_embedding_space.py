"""Frozen KB identity survives a DB round trip and cannot be retargeted."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.embedding_space import EmbeddingSpaceMismatch
from app.domain.models.knowledge_version import KnowledgeBaseVersion
from app.infrastructure.models.knowledge_base import KnowledgeBaseModel
from app.infrastructure.models.knowledge_version import KnowledgeBaseVersionORM
from app.infrastructure.repositories.db_knowledge_version_repository import (
    DBKnowledgeVersionRepository,
)
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.embedding_test_support import embedding_space
from tests.app.execution_test_support import authenticated_session_factory


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgres_integration")
async def test_postgres_candidate_space_is_write_once_and_published_space_is_immutable():
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    sessions = authenticated_session_factory(
        engine, signing_secret=settings.database_authorization_signing_secret
    )
    auth = AuthorizationContext.system("embedding-space-roundtrip")
    kb_id = f"space-kb-{uuid4()}"
    version = KnowledgeBaseVersion(knowledge_base_id=kb_id)
    space = embedding_space()
    other = space.model_copy(update={"configured_model": "different-same-dimensional-model"})
    try:
        async with sessions() as session:
            await configure_session_authorization(session, auth)
            await session.execute(
                insert(KnowledgeBaseModel).values(id=kb_id, name="space identity")
            )
            repo = DBKnowledgeVersionRepository(session)
            await repo.create_candidate(version)
            await repo.freeze_embedding_space(version.id, knowledge_base_id=kb_id, space=space)
            await session.commit()

        async with sessions() as session:
            await configure_session_authorization(session, auth)
            repo = DBKnowledgeVersionRepository(session)
            stored = await repo.get_version(version.id, knowledge_base_id=kb_id)
            assert stored is not None
            assert stored.embedding_space == space
            assert stored.embedding_space_digest == space.digest
            # Replaying the identical admission is harmless; retargeting its
            # coordinate space, including same-dimensional models, is not.
            await repo.freeze_embedding_space(version.id, knowledge_base_id=kb_id, space=space)
            with pytest.raises(EmbeddingSpaceMismatch):
                await repo.freeze_embedding_space(version.id, knowledge_base_id=kb_id, space=other)
            with pytest.raises(EmbeddingSpaceMismatch):
                await repo.freeze_embedding_space(
                    version.id, knowledge_base_id="foreign", space=space
                )
            await session.commit()

        async with sessions() as session:
            await configure_session_authorization(session, auth)
            await session.execute(
                update(KnowledgeBaseVersionORM)
                .where(KnowledgeBaseVersionORM.id == version.id)
                .values(state="ready", published_at=datetime.now(UTC))
            )
            await session.commit()
        async with sessions() as session:
            await configure_session_authorization(session, auth)
            repo = DBKnowledgeVersionRepository(session)
            with pytest.raises(EmbeddingSpaceMismatch):
                await repo.freeze_embedding_space(version.id, knowledge_base_id=kb_id, space=space)
            stored = await repo.get_version(version.id, knowledge_base_id=kb_id)
            assert stored is not None
            assert stored.embedding_space_digest == space.digest
    finally:
        async with sessions() as session:
            await configure_session_authorization(session, auth)
            await session.execute(
                delete(KnowledgeBaseVersionORM).where(KnowledgeBaseVersionORM.id == version.id)
            )
            await session.execute(delete(KnowledgeBaseModel).where(KnowledgeBaseModel.id == kb_id))
            await session.commit()
        await engine.dispose()
