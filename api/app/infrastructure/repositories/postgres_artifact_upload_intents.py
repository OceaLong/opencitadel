import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.security.authorization_context import get_authorization_context
from app.domain.models.scope import OwnerScope
from app.infrastructure.repositories.db_artifact_provenance_repository import (
    DBArtifactProvenanceRepository,
)
from app.infrastructure.security.db_authorization import configure_session_authorization


class PostgresArtifactUploadIntentWriter:
    """Uses the runtime's separate bounded pool, never the caller's write connection."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, signing_secret: str):
        self._session_factory = session_factory
        self._signing_secret = signing_secret

    async def register(
        self,
        scope: OwnerScope,
        *,
        upload_id: UUID,
        session_id: str,
        artifact_id: str,
        storage_key: str,
    ) -> None:
        async with asyncio.timeout(10), self._session_factory() as session:
            await configure_session_authorization(
                session,
                context=get_authorization_context(),
                signing_secret=self._signing_secret,
            )
            await DBArtifactProvenanceRepository(session).register_upload(
                scope,
                upload_id=upload_id,
                session_id=session_id,
                artifact_id=artifact_id,
                storage_key=storage_key,
            )
            await session.commit()
