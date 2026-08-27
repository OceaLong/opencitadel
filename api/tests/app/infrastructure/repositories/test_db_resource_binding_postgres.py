"""Canonical PostgreSQL proof for binding lock and partial-unique semantics."""

import asyncio
import uuid

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.services.resource_binding_service import (
    ResourceBindingService,
)
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.resource_bindings import (
    PublicationState,
    PublishedResourceVersion,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope
from app.domain.services.resource_version_provider import (
    ResourceVersionProviderRegistry,
)
from app.infrastructure.models.session import SessionModel
from app.infrastructure.models.user import UserORM
from app.infrastructure.repositories.db_session_repository import (
    DBSessionRepository,
)
from app.infrastructure.repositories.db_session_resource_binding_repository import (
    DBSessionResourceBindingRepository,
)
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
)
from core.config import load_deployment_settings
from tests.app.execution_test_support import authenticated_session_factory


class _Provider:
    kind = ResourceKind.CODEBASE

    def __init__(self) -> None:
        self.version_id = "cbv1"

    async def resolve_published_version(
        self,
        resource_id,
        requested_version_id,
        _scope,
    ):
        await asyncio.sleep(0)
        return PublishedResourceVersion(
            resource_kind=self.kind,
            resource_id=resource_id,
            version_id=requested_version_id or self.version_id,
            state=PublicationState.READY,
            published=True,
        )


class _BindingUow:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self._committed = False

    async def __aenter__(self):
        self.db_session = self._session_factory()
        await configure_session_authorization(
            self.db_session,
            AuthorizationContext.system("resource-binding-postgres-test"),
        )
        self.session = DBSessionRepository(self.db_session)
        self.resource_bindings = DBSessionResourceBindingRepository(self.db_session)
        return self

    async def commit(self) -> None:
        await self.db_session.commit()
        self._committed = True

    async def rollback(self) -> None:
        await self.db_session.rollback()

    async def __aexit__(self, exc_type, _exc, _tb):
        try:
            if not self._committed:
                await self.rollback()
        finally:
            await self.db_session.close()
        return False


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgres_integration")
async def test_postgres_initial_and_same_target_upgrade_races_keep_one_current():
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    session_factory = authenticated_session_factory(
        engine,
        signing_secret=settings.session_secret,
    )
    user_id = f"binding-user-{uuid.uuid4()}"
    session_id = f"binding-session-{uuid.uuid4()}"
    scope = OwnerScope.personal(user_id)
    provider = _Provider()
    service = ResourceBindingService(
        uow_factory=lambda: _BindingUow(session_factory),
        providers=ResourceVersionProviderRegistry([provider]),
    )
    try:
        async with session_factory() as setup:
            await configure_session_authorization(
                setup,
                AuthorizationContext.system("resource-binding-postgres-setup"),
            )
            await setup.execute(
                insert(UserORM).values(
                    id=user_id,
                    email=f"{user_id}@test.local",
                    username=user_id,
                )
            )
            await setup.execute(
                insert(SessionModel).values(
                    id=session_id,
                    owner_user_id=user_id,
                    status="pending",
                )
            )
            await setup.commit()

        initial_left, initial_right = await asyncio.gather(
            service.bind_initial(
                session_id,
                ResourceKind.CODEBASE,
                "cb1",
                None,
                scope,
            ),
            service.bind_initial(
                session_id,
                ResourceKind.CODEBASE,
                "cb1",
                None,
                scope,
            ),
        )
        provider.version_id = "cbv2"
        upgrade_left, upgrade_right = await asyncio.gather(
            service.upgrade(
                session_id,
                ResourceKind.CODEBASE,
                "cbv2",
                actor_id=user_id,
                scope=scope,
            ),
            service.upgrade(
                session_id,
                ResourceKind.CODEBASE,
                "cbv2",
                actor_id=user_id,
                scope=scope,
            ),
        )
        history = await service.history(
            session_id,
            ResourceKind.CODEBASE,
            scope,
        )

        assert initial_left.id == initial_right.id
        assert upgrade_left.id == upgrade_right.id
        assert [item.version_id for item in history] == ["cbv1", "cbv2"]
        assert sum(item.is_current for item in history) == 1
    finally:
        async with session_factory() as cleanup:
            await configure_session_authorization(
                cleanup,
                AuthorizationContext.system("resource-binding-postgres-cleanup"),
            )
            await cleanup.execute(delete(SessionModel).where(SessionModel.id == session_id))
            await cleanup.execute(delete(UserORM).where(UserORM.id == user_id))
            await cleanup.commit()
        await engine.dispose()
