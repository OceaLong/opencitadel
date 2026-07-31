#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Opt-in PostgreSQL proof for GC/binding mutual exclusion and parent cleanup."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.errors.exceptions import ConflictError
from app.application.services.resource_binding_service import (
    ResourceBindingService,
)
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.resource_governance import (
    BuildState,
    PublishedResourceVersion,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope
from app.domain.services.resource_version_provider import (
    ResourceVersionProviderRegistry,
)
from app.infrastructure.models.knowledge_base import KnowledgeBaseModel
from app.infrastructure.models.knowledge_version import (
    KnowledgeBaseVersionORM,
)
from app.infrastructure.models.resource_governance import (
    SessionResourceBindingORM,
)
from app.infrastructure.models.session import SessionModel
from app.infrastructure.models.user import UserORM
from app.infrastructure.repositories.db_knowledge_version_repository import (
    DBKnowledgeVersionRepository,
)
from app.infrastructure.repositories.db_uow import DBUnitOfWork
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
)
from core.config import get_settings


RUN_POSTGRES_INTEGRATION = (
    os.getenv("OPENCITADEL_RUN_POSTGRES_INTEGRATION") == "1"
)


class _PublishedProvider:
    kind = ResourceKind.KNOWLEDGE_BASE

    def __init__(self, kb_id: str, version_id: str) -> None:
        self._kb_id = kb_id
        self._version_id = version_id

    async def resolve_published_version(
        self,
        resource_id,
        requested_version_id,
        _scope,
    ):
        assert resource_id == self._kb_id
        assert requested_version_id in (None, self._version_id)
        return PublishedResourceVersion(
            resource_kind=self.kind,
            resource_id=self._kb_id,
            version_id=self._version_id,
            state=BuildState.SUCCEEDED,
            published=True,
        )


@pytest.mark.skipif(
    not RUN_POSTGRES_INTEGRATION,
    reason=(
        "set OPENCITADEL_RUN_POSTGRES_INTEGRATION=1 for PostgreSQL "
        "GC/binding lock and column-specific SET NULL proof"
    ),
)
@pytest.mark.asyncio
async def test_postgres_gc_wins_lock_then_binding_fails_without_dangling_pin(
    _db_schema,
):
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    user_id = f"gc-user-{suffix}"
    session_id = f"gc-session-{suffix}"
    kb_id = f"gc-kb-{suffix}"
    old_id = f"{kb_id}-old"
    active_id = f"{kb_id}-active"
    now = datetime.now(timezone.utc)
    system = AuthorizationContext.system("knowledge-version-gc-pg-test")
    scope = OwnerScope.personal(user_id)
    binding_task = None

    async def configured_session():
        session = session_factory()
        await configure_session_authorization(session, system)
        return session

    try:
        setup = await configured_session()
        try:
            setup.add(
                UserORM(
                    id=user_id,
                    email=f"{user_id}@example.test",
                    username=user_id,
                )
            )
            setup.add(
                SessionModel(
                    id=session_id,
                    owner_user_id=user_id,
                    status="pending",
                )
            )
            setup.add(
                KnowledgeBaseModel(
                    id=kb_id,
                    name="GC concurrency proof",
                    owner_user_id=user_id,
                    status="ready",
                )
            )
            setup.add(
                KnowledgeBaseVersionORM.from_domain(
                    KnowledgeBaseVersion(
                        id=old_id,
                        knowledge_base_id=kb_id,
                        state=KnowledgeVersionState.READY,
                        created_at=now - timedelta(days=100),
                        published_at=now - timedelta(days=100),
                    )
                )
            )
            setup.add(
                KnowledgeBaseVersionORM.from_domain(
                    KnowledgeBaseVersion(
                        id=active_id,
                        knowledge_base_id=kb_id,
                        parent_version_id=old_id,
                        state=KnowledgeVersionState.READY,
                        created_at=now - timedelta(days=1),
                        published_at=now - timedelta(days=1),
                    )
                )
            )
            await setup.flush()
            (await setup.get(KnowledgeBaseModel, kb_id)).active_version_id = (
                active_id
            )
            await setup.commit()
        finally:
            await setup.close()

        gc_session = await configured_session()
        try:
            await gc_session.execute(
                select(KnowledgeBaseModel)
                .where(KnowledgeBaseModel.id == kb_id)
                .with_for_update()
            )
            service = ResourceBindingService(
                uow_factory=lambda: DBUnitOfWork(
                    session_factory,
                    authorization_context=system,
                ),
                providers=ResourceVersionProviderRegistry(
                    [_PublishedProvider(kb_id, old_id)]
                ),
            )
            binding_task = asyncio.create_task(
                service.bind_initial(
                    session_id,
                    ResourceKind.KNOWLEDGE_BASE,
                    kb_id,
                    old_id,
                    scope,
                )
            )
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.shield(binding_task),
                    timeout=0.1,
                )

            result = await DBKnowledgeVersionRepository(
                gc_session
            ).collect_garbage(
                retain_count=0,
                older_than=now,
                batch_size=10,
            )
            await gc_session.commit()
        finally:
            await gc_session.close()

        assert result.collected_version_ids == (old_id,)
        with pytest.raises(ConflictError, match="no longer"):
            await asyncio.wait_for(binding_task, timeout=5)

        verification = await configured_session()
        try:
            active = await verification.get(
                KnowledgeBaseVersionORM,
                active_id,
            )
            assert active.parent_version_id is None
            assert (
                await verification.scalar(
                    select(SessionResourceBindingORM.id).where(
                        SessionResourceBindingORM.session_id == session_id
                    )
                )
                is None
            )
        finally:
            await verification.close()
    finally:
        if binding_task is not None and not binding_task.done():
            binding_task.cancel()
            await asyncio.gather(binding_task, return_exceptions=True)
        cleanup = await configured_session()
        try:
            await cleanup.execute(
                delete(SessionModel).where(SessionModel.id == session_id)
            )
            await cleanup.execute(
                delete(KnowledgeBaseModel).where(
                    KnowledgeBaseModel.id == kb_id
                )
            )
            await cleanup.execute(
                delete(UserORM).where(UserORM.id == user_id)
            )
            await cleanup.commit()
        finally:
            await cleanup.close()
            await engine.dispose()
