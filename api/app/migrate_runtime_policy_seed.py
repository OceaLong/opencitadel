"""Typed, atomic initial Runtime Policy seed for the migration process."""

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.models.authorization import AuthorizationContext
from app.domain.runtime_policy import ExecutionPolicy, OperationsPolicy
from app.infrastructure.repositories.postgres_runtime_policy_repository import (
    PostgresRuntimePolicyRepository,
)
from core.config import DeploymentSettings, sqlalchemy_sync_migration_database_uri


async def seed_runtime_policy_heads(settings: DeploymentSettings) -> bool:
    sync_url = make_url(sqlalchemy_sync_migration_database_uri(settings))
    async_url = sync_url.set(drivername="postgresql+asyncpg")
    engine = create_async_engine(async_url, pool_pre_ping=True)
    sessions = async_sessionmaker(
        engine,
        expire_on_commit=False,
        info={
            "database_authorization_signing_secret": (
                settings.database_authorization_signing_secret
            ),
        },
    )
    repository = PostgresRuntimePolicyRepository(
        session_factory=sessions,
        authorization=AuthorizationContext.system("migration:runtime-policy-seed"),
    )
    try:
        return await repository.seed_if_missing(
            execution_policy=ExecutionPolicy(),
            operations_policy=OperationsPolicy(),
            actor="migration:runtime-policy-seed",
            note="greenfield initial Runtime Policy",
        )
    finally:
        await engine.dispose()


__all__ = ["seed_runtime_policy_heads"]
