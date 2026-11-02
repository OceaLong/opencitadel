"""Publish the exact running worker's capabilities in signed kernel transactions."""

from contextlib import asynccontextmanager
from uuid import uuid4

from app.application.execution.definition_capabilities import DefinitionCapabilityPublisher
from app.composition.execution_definitions import worker_capabilities
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.postgres_definition_store import PostgresDefinitionStore
from app.infrastructure.security.db_authorization import configure_session_authorization


def build_definition_publisher(*, sessions, definitions, worker_id):
    @asynccontextmanager
    async def stores():
        async with sessions() as session:
            await configure_session_authorization(
                session, AuthorizationContext.system("execution-kernel")
            )
            yield PostgresDefinitionStore(session)
            await session.commit()

    return DefinitionCapabilityPublisher(
        store_context=stores,
        definitions=definitions,
        capabilities=worker_capabilities(definitions, worker_id=worker_id, boot_id=str(uuid4())),
    )
