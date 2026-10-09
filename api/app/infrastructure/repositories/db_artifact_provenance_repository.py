from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.artifact_provenance import ArtifactProducer, ArtifactVersionProvenance
from app.domain.models.scope import OwnerScope
from app.infrastructure.models.execution_view import ArtifactVersionProvenanceORM as Provenance


def scope_values(scope: OwnerScope) -> dict[str, str | None]:
    return {"owner_user_id": None if scope.team_id else scope.user_id, "team_id": scope.team_id}


def record_from_row(row: Provenance) -> ArtifactVersionProvenance:
    return ArtifactVersionProvenance(
        **{name: getattr(row, name) for name in ArtifactVersionProvenance.model_fields}
    )


class DBArtifactProvenanceRepository:
    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def lock_artifact(self, artifact_id: str) -> None:
        await self.db_session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "artifact-version:" + artifact_id},
        )

    async def lock_upload(self, upload_id: UUID) -> None:
        await self.db_session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "artifact-upload:" + str(upload_id)},
        )

    async def session_scope(self, session_id: str) -> OwnerScope:
        row = (
            await self.db_session.execute(
                text("SELECT owner_user_id,team_id FROM sessions WHERE id=:id"), {"id": session_id}
            )
        ).one_or_none()
        if row is None:
            raise PermissionError("artifact session unavailable")
        return (
            OwnerScope.team(row.owner_user_id or "system", row.team_id)
            if row.team_id
            else OwnerScope.personal(row.owner_user_id)
        )

    async def register_upload(self, scope, *, upload_id, session_id, artifact_id, storage_key):
        await self.db_session.execute(
            text("""INSERT INTO artifact_upload_intents(upload_id,session_id,artifact_id,storage_key,owner_user_id,team_id,created_by)
        VALUES(:upload_id,:session_id,:artifact_id,:storage_key,:owner_user_id,:team_id,'artifact-service')"""),
            dict(
                upload_id=upload_id,
                session_id=session_id,
                artifact_id=artifact_id,
                storage_key=storage_key,
                **scope_values(scope),
            ),
        )

    async def record_version(
        self,
        scope: OwnerScope,
        provenance: ArtifactVersionProvenance,
        *,
        producer: ArtifactProducer | None = None,
        storage_key: str,
    ):
        await self.db_session.flush()
        self.db_session.add(
            Provenance(
                **provenance.model_dump(),
                **scope_values(scope),
                created_by="artifact-service",
                revision=0,
            )
        )
        await self.db_session.flush()
        if producer is not None:
            if scope_values(scope) != scope_values(producer.scope):
                raise PermissionError("producer scope mismatch")
            await self.db_session.execute(
                text("""INSERT INTO artifact_production_receipts
                (operation_id,association_id,artifact_id,version,run_id,activity_id,generation,claim_generation,invocation_id,storage_key,content_digest,owner_user_id,team_id,created_by)
                VALUES(:operation_id,:association_id,:artifact_id,:version,:run_id,:activity_id,:generation,:claim_generation,:invocation_id,:storage_key,:content_digest,:owner_user_id,:team_id,'artifact-service')"""),
                dict(
                    operation_id=producer.operation_id,
                    association_id=provenance.id,
                    artifact_id=provenance.artifact_id,
                    version=provenance.version,
                    run_id=producer.run_id,
                    activity_id=producer.activity_id,
                    generation=producer.generation,
                    claim_generation=producer.claim_generation,
                    invocation_id=producer.invocation_id,
                    storage_key=storage_key,
                    content_digest=provenance.content_digest,
                    **scope_values(scope),
                ),
            )

    def scoped(self, scope):
        return select(Provenance).where(
            Provenance.owner_user_id.is_not_distinct_from(scope_values(scope)["owner_user_id"]),
            Provenance.team_id.is_not_distinct_from(scope.team_id),
        )

    async def get_version(
        self, scope: OwnerScope, artifact_id: str, version: int
    ) -> list[ArtifactVersionProvenance]:
        rows = await self.db_session.scalars(
            self.scoped(scope)
            .where(Provenance.artifact_id == artifact_id, Provenance.version == version)
            .order_by(Provenance.producer_identity)
        )
        return [record_from_row(row) for row in rows]

    async def get_operation(
        self, scope: OwnerScope, operation_id: UUID
    ) -> ArtifactVersionProvenance | None:
        row = await self.db_session.scalar(
            self.scoped(scope).where(Provenance.producer_identity == str(operation_id))
        )
        return record_from_row(row) if row else None
