from typing import Protocol
from uuid import UUID

from app.domain.models.scope import OwnerScope


class ArtifactUploadIntentWriter(Protocol):
    """Durably record cleanup intent independently of the locked artifact transaction."""

    async def register(
        self,
        scope: OwnerScope,
        *,
        upload_id: UUID,
        session_id: str,
        artifact_id: str,
        storage_key: str,
    ) -> None: ...
