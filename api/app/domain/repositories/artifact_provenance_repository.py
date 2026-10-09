from typing import Protocol
from uuid import UUID

from app.domain.models.artifact_provenance import ArtifactProducer, ArtifactVersionProvenance
from app.domain.models.scope import OwnerScope


class ArtifactProvenanceRepository(Protocol):
    async def lock_artifact(self, artifact_id: str) -> None: ...
    async def lock_upload(self, upload_id: UUID) -> None: ...
    async def session_scope(self, session_id: str) -> OwnerScope: ...
    async def register_upload(
        self,
        scope: OwnerScope,
        *,
        upload_id: UUID,
        session_id: str,
        artifact_id: str,
        storage_key: str,
    ) -> None: ...
    async def record_version(
        self,
        scope: OwnerScope,
        provenance: ArtifactVersionProvenance,
        *,
        producer: ArtifactProducer | None = None,
        storage_key: str,
    ) -> None: ...
    async def get_version(
        self, scope: OwnerScope, artifact_id: str, version: int
    ) -> list[ArtifactVersionProvenance]: ...
    async def get_operation(
        self, scope: OwnerScope, operation_id: UUID
    ) -> ArtifactVersionProvenance | None: ...
