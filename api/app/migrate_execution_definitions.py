"""Seed missing definition heads under the enclosing schema migration lock."""

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.composition.execution_definitions import build_code_definitions, initial_definition_heads
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.postgres_definition_store import PostgresDefinitionStore
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import sqlalchemy_sync_migration_database_uri


async def seed_execution_definition_heads(settings):
    engine = create_async_engine(
        make_url(sqlalchemy_sync_migration_database_uri(settings)).set(
            drivername="postgresql+asyncpg"
        ),
        pool_pre_ping=True,
    )
    sessions = async_sessionmaker(
        engine,
        expire_on_commit=False,
        info={
            "database_authorization_signing_secret": settings.database_authorization_signing_secret
        },
    )
    try:
        async with sessions() as session:
            await configure_session_authorization(
                session, AuthorizationContext.system("execution-kernel")
            )
            store = PostgresDefinitionStore(session)
            definitions = build_code_definitions()
            for manifest in definitions.manifests:
                await store.register(manifest)
            for manifest in initial_definition_heads(definitions):
                # Existing heads are never moved. An artifact change under the
                # same released identity is an error, including on repeated seed.
                await store.seed_head(manifest)
            await session.commit()
            return len(definitions.manifests)
    finally:
        await engine.dispose()
