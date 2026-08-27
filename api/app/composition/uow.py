from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.crypto import VersionedSecretCipher
from app.domain.models.authorization import AuthorizationContext
from app.domain.repositories.uow import UnitOfWorkFactory
from app.infrastructure.repositories.db_uow import DBUnitOfWork


@dataclass(frozen=True)
class DBUnitOfWorkDependencies:
    secret_cipher: VersionedSecretCipher
    audit_signing_key: str
    audit_signing_key_id: str
    database_authorization_signing_secret: str
    cleanup_timeout_seconds: float = 10.0


def create_uow_factory(
    *,
    session_factory: Callable[[], AsyncSession],
    dependencies: DBUnitOfWorkDependencies,
) -> UnitOfWorkFactory:
    def factory(
        authorization_context: AuthorizationContext | None = None,
    ) -> DBUnitOfWork:
        return DBUnitOfWork(
            session_factory,
            secret_cipher=dependencies.secret_cipher,
            audit_signing_key=dependencies.audit_signing_key,
            audit_signing_key_id=dependencies.audit_signing_key_id,
            database_authorization_signing_secret=(
                dependencies.database_authorization_signing_secret
            ),
            authorization_context=authorization_context,
            cleanup_timeout_seconds=dependencies.cleanup_timeout_seconds,
        )

    return factory
