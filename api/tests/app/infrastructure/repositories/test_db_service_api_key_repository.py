"""Real PostgreSQL proof for service API key expiry (F16a).

Exercises ``DBServiceApiKeyRepository.get_by_hash`` against a live Postgres to
prove the authentication lookup rejects expired keys (``expires_at`` in the
past) while still honouring never-expiring keys (``expires_at IS NULL``) and
future expiry, alongside the pre-existing revocation filter.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.service_api_key import ServiceApiKey
from app.infrastructure.models.user import UserORM
from app.infrastructure.repositories.db_service_api_key_repository import (
    DBServiceApiKeyRepository,
)
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.execution_test_support import authenticated_session_factory


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgres_integration")
async def test_get_by_hash_filters_expired_and_revoked_keys() -> None:
    settings = load_deployment_settings()
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    session_factory = authenticated_session_factory(engine, signing_secret=settings.session_secret)

    owner_id = f"apikey-user-{uuid.uuid4()}"
    now = datetime.now(UTC)

    def _key(suffix: str, *, expires_at=None, revoked_at=None) -> ServiceApiKey:
        return ServiceApiKey(
            id=f"{owner_id}-{suffix}",
            owner_user_id=owner_id,
            name=f"key-{suffix}",
            key_hash=f"hash-{owner_id}-{suffix}",
            prefix="oc_test",
            expires_at=expires_at,
            revoked_at=revoked_at,
        )

    never = _key("never")
    future = _key("future", expires_at=now + timedelta(hours=1))
    expired = _key("expired", expires_at=now - timedelta(hours=1))
    revoked = _key("revoked", revoked_at=now - timedelta(minutes=5))

    try:
        async with session_factory() as setup:
            await configure_session_authorization(
                setup, AuthorizationContext.system("apikey-setup")
            )
            await setup.execute(
                insert(UserORM).values(
                    id=owner_id, email=f"{owner_id}@test.local", username=owner_id
                )
            )
            repo = DBServiceApiKeyRepository(setup)
            for key in (never, future, expired, revoked):
                await repo.save(key)
            await setup.commit()

        async with session_factory() as read:
            await configure_session_authorization(read, AuthorizationContext.system("apikey-read"))
            repo = DBServiceApiKeyRepository(read)

            assert (await repo.get_by_hash(never.key_hash)) is not None
            assert (await repo.get_by_hash(future.key_hash)) is not None
            # Expired keys are treated as invalid for authentication.
            assert (await repo.get_by_hash(expired.key_hash)) is None
            # Pre-existing revocation filter still holds.
            assert (await repo.get_by_hash(revoked.key_hash)) is None
    finally:
        await engine.dispose()
