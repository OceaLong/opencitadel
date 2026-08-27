"""SQLAlchemy persistence for immutable knowledge-base versions."""

from datetime import UTC, datetime

from sqlalchemy import and_, delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeBaseVersion,
    KnowledgeDocumentRevision,
    KnowledgeVersionDocument,
    KnowledgeVersionState,
    mutable_json_value,
)
from app.domain.models.resource_bindings import ResourceKind
from app.domain.repositories.knowledge_version_repository import (
    KnowledgeVersionGCResult,
    KnowledgeVersionRepository,
)
from app.domain.repositories.patch import UNSET, UnsetType
from app.infrastructure.models.knowledge_base import (
    KnowledgeBaseModel,
    KnowledgeChunkModel,
    KnowledgeDocumentModel,
    KnowledgeEntityModel,
    KnowledgeEntityRefModel,
    KnowledgeRelationModel,
)
from app.infrastructure.models.knowledge_version import (
    KnowledgeBaseVersionORM,
    KnowledgeDocumentRevisionORM,
    KnowledgeVersionDocumentORM,
)
from app.infrastructure.repositories.version_gc_base import (
    VersionGarbageCollector,
)

_DOCUMENT_TRANSITIONS = {
    DocumentRevisionState.UPLOADED: {
        DocumentRevisionState.UPLOADED,
        DocumentRevisionState.PARSING,
        DocumentRevisionState.FAILED,
    },
    DocumentRevisionState.PARSING: {
        DocumentRevisionState.PARSING,
        DocumentRevisionState.PARSED,
        DocumentRevisionState.FAILED,
    },
    DocumentRevisionState.PARSED: {
        DocumentRevisionState.PARSED,
        DocumentRevisionState.INDEXING,
        DocumentRevisionState.FAILED,
    },
    DocumentRevisionState.INDEXING: {
        DocumentRevisionState.INDEXING,
        DocumentRevisionState.INDEXED,
        DocumentRevisionState.FAILED,
    },
    DocumentRevisionState.INDEXED: {
        DocumentRevisionState.INDEXED,
    },
    # A failed, unpublished revision may be retried by a later/re-entered
    # candidate without allocating a second content identity.
    DocumentRevisionState.FAILED: {
        DocumentRevisionState.FAILED,
        DocumentRevisionState.PARSING,
    },
}


def _require_document_transition(
    current: DocumentRevisionState,
    target: DocumentRevisionState,
) -> None:
    if target not in _DOCUMENT_TRANSITIONS[current]:
        raise ValueError(f"invalid document revision transition {current.value}->{target.value}")


class DBKnowledgeVersionRepository(
    VersionGarbageCollector,
    KnowledgeVersionRepository,
):
    """All writes participate in the caller's Unit of Work transaction."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    @property
    def _version_model(self):
        return KnowledgeBaseVersionORM

    @property
    def _resource_model(self):
        return KnowledgeBaseModel

    @property
    def _resource_fk_column(self):
        return KnowledgeBaseVersionORM.knowledge_base_id

    @property
    def _ranked_cte_name(self) -> str:
        return "ranked_knowledge_versions"

    @property
    def _resource_kind(self) -> str:
        return ResourceKind.KNOWLEDGE_BASE.value

    def _empty_totals(self) -> dict:
        return {
            "deleted_manifests": 0,
            "deleted_revisions": 0,
            "deleted_chunks": 0,
            "reclaimed_logical_bytes": 0,
            "deleted_entities": 0,
            "deleted_relations": 0,
            "deleted_entity_refs": 0,
            "retained_shared_revisions": 0,
        }

    def _empty_extras(self) -> dict:
        return {}

    def _accumulate_deleted(self, totals, extras, deleted) -> None:
        for name, count in deleted.items():
            totals[name] += count

    def _build_gc_result(self, *, collected, protected, totals, extras):
        return KnowledgeVersionGCResult(
            collected_version_ids=tuple(collected),
            deleted_versions=len(collected),
            protected_active_versions=protected["active"],
            protected_bound_versions=protected["bound"],
            protected_building_versions=protected["building"],
            protected_age_versions=protected["age"],
            protected_retention_versions=protected["retention"],
            **totals,
        )

    async def _delete_version_closure(
        self,
        version: KnowledgeBaseVersionORM,
    ) -> dict[str, int]:
        version_id = version.id
        knowledge_base_id = version.knowledge_base_id
        revision_result = await self.db_session.execute(
            select(KnowledgeVersionDocumentORM.document_revision_id).where(
                KnowledgeVersionDocumentORM.version_id == version_id,
                KnowledgeVersionDocumentORM.knowledge_base_id == knowledge_base_id,
            )
        )
        revision_ids = tuple(dict.fromkeys(revision_result.scalars().all()))
        chunk_result = await self.db_session.execute(
            select(
                KnowledgeChunkModel.id,
                KnowledgeChunkModel.content,
            ).where(
                KnowledgeChunkModel.version_id == version_id,
                KnowledgeChunkModel.kb_id == knowledge_base_id,
            )
        )
        chunk_rows = chunk_result.all()
        chunk_ids = tuple(row.id for row in chunk_rows)
        reclaimed_logical_bytes = sum(len(row.content.encode("utf-8")) for row in chunk_rows)
        if chunk_ids:
            external_child_result = await self.db_session.execute(
                select(KnowledgeChunkModel.id)
                .where(
                    KnowledgeChunkModel.parent_id.in_(chunk_ids),
                    or_(
                        KnowledgeChunkModel.version_id != version_id,
                        KnowledgeChunkModel.version_id.is_(None),
                    ),
                )
                .limit(1)
            )
            if external_child_result.scalar_one_or_none() is not None:
                raise RuntimeError("version GC found a cross-version chunk parent reference")

        deleted_relations = await self._delete_returning_count(
            delete(KnowledgeRelationModel)
            .where(
                KnowledgeRelationModel.version_id == version_id,
                KnowledgeRelationModel.kb_id == knowledge_base_id,
            )
            .returning(KnowledgeRelationModel.id)
        )
        deleted_entity_refs = await self._delete_returning_count(
            delete(KnowledgeEntityRefModel)
            .where(
                KnowledgeEntityRefModel.version_id == version_id,
                KnowledgeEntityRefModel.kb_id == knowledge_base_id,
            )
            .returning(KnowledgeEntityRefModel.id)
        )
        deleted_chunks = await self._delete_returning_count(
            delete(KnowledgeChunkModel)
            .where(
                KnowledgeChunkModel.version_id == version_id,
                KnowledgeChunkModel.kb_id == knowledge_base_id,
            )
            .returning(KnowledgeChunkModel.id)
        )
        deleted_entities = await self._delete_returning_count(
            delete(KnowledgeEntityModel)
            .where(
                KnowledgeEntityModel.version_id == version_id,
                KnowledgeEntityModel.kb_id == knowledge_base_id,
            )
            .returning(KnowledgeEntityModel.id)
        )
        manifest_result = await self.db_session.execute(
            delete(KnowledgeVersionDocumentORM)
            .where(
                KnowledgeVersionDocumentORM.version_id == version_id,
                KnowledgeVersionDocumentORM.knowledge_base_id == knowledge_base_id,
            )
            .returning(
                KnowledgeVersionDocumentORM.version_id,
                KnowledgeVersionDocumentORM.document_id,
            )
        )
        deleted_manifests = len(manifest_result.all())

        deleted_revisions = 0
        retained_shared_revisions = 0
        if revision_ids:
            shared_result = await self.db_session.execute(
                select(KnowledgeVersionDocumentORM.document_revision_id).where(
                    KnowledgeVersionDocumentORM.document_revision_id.in_(revision_ids)
                )
            )
            shared_ids = set(shared_result.scalars().all())
            retained_shared_revisions = len(shared_ids)
            orphaned_ids = tuple(
                revision_id for revision_id in revision_ids if revision_id not in shared_ids
            )
            if orphaned_ids:
                deleted_revisions = await self._delete_returning_count(
                    delete(KnowledgeDocumentRevisionORM)
                    .where(
                        KnowledgeDocumentRevisionORM.id.in_(orphaned_ids),
                        ~exists(
                            select(KnowledgeVersionDocumentORM.version_id).where(
                                KnowledgeVersionDocumentORM.document_revision_id
                                == KnowledgeDocumentRevisionORM.id
                            )
                        ),
                    )
                    .returning(KnowledgeDocumentRevisionORM.id)
                )

        deleted_versions = await self._delete_returning_count(
            delete(KnowledgeBaseVersionORM)
            .where(
                KnowledgeBaseVersionORM.id == version_id,
                KnowledgeBaseVersionORM.knowledge_base_id == knowledge_base_id,
            )
            .returning(KnowledgeBaseVersionORM.id)
        )
        if deleted_versions != 1:
            raise RuntimeError("version GC lost its locked candidate")
        return {
            "deleted_manifests": deleted_manifests,
            "deleted_revisions": deleted_revisions,
            "deleted_chunks": deleted_chunks,
            "reclaimed_logical_bytes": reclaimed_logical_bytes,
            "deleted_entities": deleted_entities,
            "deleted_relations": deleted_relations,
            "deleted_entity_refs": deleted_entity_refs,
            "retained_shared_revisions": retained_shared_revisions,
        }

    async def create_candidate(
        self,
        version: KnowledgeBaseVersion,
    ) -> KnowledgeBaseVersion:
        if version.state is not KnowledgeVersionState.BUILDING:
            raise ValueError("candidate must start in building state")
        if version.published_at is not None:
            raise ValueError("candidate cannot already be published")

        kb_result = await self.db_session.execute(
            select(KnowledgeBaseModel.id).where(KnowledgeBaseModel.id == version.knowledge_base_id)
        )
        if kb_result.scalar_one_or_none() is None:
            raise ValueError("candidate knowledge base does not exist")

        if version.parent_version_id is not None:
            parent_result = await self.db_session.execute(
                select(KnowledgeBaseVersionORM.id).where(
                    KnowledgeBaseVersionORM.id == version.parent_version_id,
                    KnowledgeBaseVersionORM.knowledge_base_id == version.knowledge_base_id,
                )
            )
            if parent_result.scalar_one_or_none() is None:
                raise ValueError("candidate parent must belong to the same knowledge base")

        if not version.build_id or not version.build_id.strip():
            raise ValueError("candidate requires an active resource build")
        self.db_session.add(KnowledgeBaseVersionORM.from_domain(version))
        await self.db_session.flush()
        return version.model_copy(deep=True)

    async def get_version(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
    ) -> KnowledgeBaseVersion | None:
        result = await self.db_session.execute(
            select(KnowledgeBaseVersionORM).where(
                KnowledgeBaseVersionORM.id == version_id,
                KnowledgeBaseVersionORM.knowledge_base_id == knowledge_base_id,
            )
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def get_active_candidate(
        self,
        knowledge_base_id: str,
    ) -> KnowledgeBaseVersion | None:
        result = await self.db_session.execute(
            select(KnowledgeBaseVersionORM).where(
                KnowledgeBaseVersionORM.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseVersionORM.state == KnowledgeVersionState.BUILDING.value,
            )
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def update_candidate_metrics(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
        metrics: dict,
    ) -> bool:
        result = await self.db_session.execute(
            select(KnowledgeBaseVersionORM)
            .where(
                KnowledgeBaseVersionORM.id == version_id,
                KnowledgeBaseVersionORM.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseVersionORM.state == KnowledgeVersionState.BUILDING.value,
            )
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None:
            return False
        record.metrics = {**dict(record.metrics or {}), **dict(metrics)}
        await self.db_session.flush()
        return True

    async def list_versions(
        self,
        knowledge_base_id: str,
        *,
        limit: int = 500,
        before: tuple[datetime, str] | None = None,
    ) -> list[KnowledgeBaseVersion]:
        if limit < 1 or limit > 500:
            raise ValueError("version list limit must be between 1 and 500")
        stmt = (
            select(KnowledgeBaseVersionORM)
            .where(KnowledgeBaseVersionORM.knowledge_base_id == knowledge_base_id)
            .order_by(
                KnowledgeBaseVersionORM.created_at.desc(),
                KnowledgeBaseVersionORM.id.desc(),
            )
            .limit(limit)
        )
        if before is not None:
            cursor_created_at, cursor_id = before
            if not isinstance(cursor_created_at, datetime) or not cursor_id:
                raise ValueError("invalid knowledge-version cursor")
            stmt = stmt.where(
                or_(
                    KnowledgeBaseVersionORM.created_at < cursor_created_at,
                    and_(
                        KnowledgeBaseVersionORM.created_at == cursor_created_at,
                        KnowledgeBaseVersionORM.id < cursor_id,
                    ),
                )
            )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def get_manifest(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
    ) -> list[KnowledgeVersionDocument]:
        result = await self.db_session.execute(
            select(KnowledgeVersionDocumentORM)
            .join(
                KnowledgeBaseVersionORM,
                (KnowledgeBaseVersionORM.id == KnowledgeVersionDocumentORM.version_id)
                & (
                    KnowledgeBaseVersionORM.knowledge_base_id
                    == KnowledgeVersionDocumentORM.knowledge_base_id
                ),
            )
            .where(
                KnowledgeVersionDocumentORM.version_id == version_id,
                KnowledgeVersionDocumentORM.knowledge_base_id == knowledge_base_id,
            )
            .order_by(
                KnowledgeVersionDocumentORM.ordinal.asc(),
                KnowledgeVersionDocumentORM.document_id.asc(),
            )
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def get_build_candidate(
        self,
        build_id: str,
    ) -> (
        tuple[
            KnowledgeBaseVersion,
            list[KnowledgeVersionDocument],
        ]
        | None
    ):
        result = await self.db_session.execute(
            select(KnowledgeBaseVersionORM).where(KnowledgeBaseVersionORM.build_id == build_id)
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None
        version = record.to_domain()
        manifest = await self.get_manifest(
            version.id,
            knowledge_base_id=version.knowledge_base_id,
        )
        return version, manifest

    async def transition_document(
        self,
        version_id: str,
        document_id: str,
        *,
        knowledge_base_id: str,
        state: DocumentRevisionState,
        parsed_blocks: list[dict] | UnsetType | None = UNSET,
        page_count: int | UnsetType | None = UNSET,
        error: str | UnsetType | None = UNSET,
        warning: str | UnsetType | None = UNSET,
    ) -> KnowledgeVersionDocument:
        target_state = DocumentRevisionState(state)
        version_result = await self.db_session.execute(
            select(KnowledgeBaseVersionORM)
            .where(
                KnowledgeBaseVersionORM.id == version_id,
                KnowledgeBaseVersionORM.knowledge_base_id == knowledge_base_id,
            )
            .with_for_update()
        )
        version = version_result.scalar_one_or_none()
        if (
            version is None
            or version.state != KnowledgeVersionState.BUILDING.value
            or version.published_at is not None
        ):
            raise ValueError("document transition requires a building candidate")

        manifest_result = await self.db_session.execute(
            select(KnowledgeVersionDocumentORM)
            .where(
                KnowledgeVersionDocumentORM.version_id == version_id,
                KnowledgeVersionDocumentORM.knowledge_base_id == knowledge_base_id,
                KnowledgeVersionDocumentORM.document_id == document_id,
            )
            .with_for_update()
        )
        manifest = manifest_result.scalar_one_or_none()
        if manifest is None:
            raise ValueError("document transition requires candidate manifest membership")

        revision_result = await self.db_session.execute(
            select(KnowledgeDocumentRevisionORM)
            .where(
                KnowledgeDocumentRevisionORM.id == manifest.document_revision_id,
                KnowledgeDocumentRevisionORM.document_id == document_id,
            )
            .with_for_update()
        )
        revision = revision_result.scalar_one_or_none()
        if revision is None:
            raise ValueError("candidate revision closure is incomplete")

        current_manifest_state = DocumentRevisionState(manifest.state)
        current_revision_state = DocumentRevisionState(revision.state)
        _require_document_transition(current_manifest_state, target_state)
        if current_revision_state is not DocumentRevisionState.INDEXED:
            _require_document_transition(current_revision_state, target_state)
            revision.state = target_state.value
            if parsed_blocks is not UNSET and parsed_blocks is not None:
                revision.parsed_blocks = mutable_json_value(parsed_blocks)
            if page_count is not UNSET and page_count is not None:
                revision.page_count = page_count
            if error is not UNSET:
                revision.error = error
            if warning is not UNSET:
                revision.warning = warning

        manifest.state = target_state.value
        if error is not UNSET:
            manifest.error = error
        if warning is not UNSET:
            manifest.warning = warning
        await self.db_session.flush()
        return manifest.to_domain()

    async def add_revision(
        self,
        revision: KnowledgeDocumentRevision,
        *,
        knowledge_base_id: str,
    ) -> KnowledgeDocumentRevision:
        owner_result = await self.db_session.execute(
            select(KnowledgeDocumentModel.id).where(
                KnowledgeDocumentModel.id == revision.document_id,
                KnowledgeDocumentModel.kb_id == knowledge_base_id,
            )
        )
        if owner_result.scalar_one_or_none() is None:
            raise ValueError("revision document must belong to the knowledge base")
        self.db_session.add(KnowledgeDocumentRevisionORM.from_domain(revision))
        await self.db_session.flush()
        return revision.model_copy(deep=True)

    async def get_revisions(
        self,
        revision_ids: list[str],
        *,
        knowledge_base_id: str,
    ) -> dict[str, KnowledgeDocumentRevision]:
        if not revision_ids:
            return {}
        result = await self.db_session.execute(
            select(KnowledgeDocumentRevisionORM)
            .join(
                KnowledgeDocumentModel,
                KnowledgeDocumentModel.id == KnowledgeDocumentRevisionORM.document_id,
            )
            .where(
                KnowledgeDocumentRevisionORM.id.in_(revision_ids),
                KnowledgeDocumentModel.kb_id == knowledge_base_id,
            )
        )
        return {record.id: record.to_domain() for record in result.scalars().all()}

    async def get_revision_by_digest(
        self,
        document_id: str,
        source_digest: str,
        *,
        knowledge_base_id: str,
    ) -> KnowledgeDocumentRevision | None:
        result = await self.db_session.execute(
            select(KnowledgeDocumentRevisionORM)
            .join(
                KnowledgeDocumentModel,
                KnowledgeDocumentModel.id == KnowledgeDocumentRevisionORM.document_id,
            )
            .where(
                KnowledgeDocumentRevisionORM.document_id == document_id,
                KnowledgeDocumentRevisionORM.source_digest == source_digest,
                KnowledgeDocumentModel.kb_id == knowledge_base_id,
            )
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def add_manifest(
        self,
        version_id: str,
        documents: list[KnowledgeVersionDocument],
        *,
        knowledge_base_id: str,
    ) -> None:
        version_result = await self.db_session.execute(
            select(KnowledgeBaseVersionORM.id).where(
                KnowledgeBaseVersionORM.id == version_id,
                KnowledgeBaseVersionORM.knowledge_base_id == knowledge_base_id,
            )
        )
        if version_result.scalar_one_or_none() is None:
            raise ValueError("manifest version must belong to the knowledge base")
        if any(item.version_id != version_id for item in documents):
            raise ValueError("manifest entries must target the candidate")
        ordinals = [item.ordinal for item in documents]
        if ordinals != list(range(len(documents))):
            raise ValueError("manifest ordinals must be unique and contiguous from zero")
        document_ids = [item.document_id for item in documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("manifest logical document ids must be unique")
        if documents:
            owners_result = await self.db_session.execute(
                select(KnowledgeDocumentModel.id).where(
                    KnowledgeDocumentModel.id.in_(document_ids),
                    KnowledgeDocumentModel.kb_id == knowledge_base_id,
                )
            )
            if set(owners_result.scalars().all()) != set(document_ids):
                raise ValueError("manifest documents must belong to the knowledge base")
        self.db_session.add_all(
            [
                KnowledgeVersionDocumentORM.from_domain(
                    item,
                    knowledge_base_id=knowledge_base_id,
                )
                for item in documents
            ]
        )
        await self.db_session.flush()

    async def publish_candidate(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
        expected_active_version_id: str | None,
        state: KnowledgeVersionState,
        capabilities: dict[str, bool],
        degraded_reasons: list[str],
        metrics: dict[str, int | float],
    ) -> bool:
        try:
            state = KnowledgeVersionState(state)
        except (TypeError, ValueError) as exc:
            raise ValueError("published candidate must be ready or degraded") from exc
        if state not in {
            KnowledgeVersionState.READY,
            KnowledgeVersionState.DEGRADED,
        }:
            raise ValueError("published candidate must be ready or degraded")
        if state is KnowledgeVersionState.READY and degraded_reasons:
            raise ValueError("ready candidate cannot have degradation reasons")
        if state is KnowledgeVersionState.DEGRADED and (
            not degraded_reasons
            or any(not isinstance(reason, str) or not reason.strip() for reason in degraded_reasons)
        ):
            raise ValueError("degraded candidate requires a stable degradation reason")

        candidate_identity_result = await self.db_session.execute(
            select(
                KnowledgeBaseVersionORM.id,
                KnowledgeBaseVersionORM.knowledge_base_id,
            ).where(
                KnowledgeBaseVersionORM.id == version_id,
                KnowledgeBaseVersionORM.knowledge_base_id == knowledge_base_id,
            )
        )
        candidate_identity = candidate_identity_result.one_or_none()
        if candidate_identity is None:
            return False

        kb_result = await self.db_session.execute(
            select(KnowledgeBaseModel)
            .where(KnowledgeBaseModel.id == knowledge_base_id)
            .with_for_update()
        )
        kb = kb_result.scalar_one_or_none()
        if kb is None or kb.active_version_id != expected_active_version_id:
            return False

        candidate_result = await self.db_session.execute(
            select(KnowledgeBaseVersionORM)
            .where(
                KnowledgeBaseVersionORM.id == version_id,
                KnowledgeBaseVersionORM.knowledge_base_id == kb.id,
            )
            .with_for_update()
        )
        candidate = candidate_result.scalar_one_or_none()
        if (
            candidate is None
            or candidate.knowledge_base_id != kb.id
            or candidate.parent_version_id != expected_active_version_id
            or candidate.state != KnowledgeVersionState.BUILDING.value
            or candidate.published_at is not None
        ):
            return False

        candidate.state = state.value
        candidate.capabilities = mutable_json_value(capabilities)
        candidate.degraded_reasons = mutable_json_value(degraded_reasons)
        candidate.metrics = mutable_json_value(metrics)
        candidate.published_at = datetime.now(UTC)
        kb.active_version_id = candidate.id
        await self.db_session.flush()
        return True

    async def fail_candidate(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
        metrics: dict[str, int | float] | None = None,
    ) -> bool:
        result = await self.db_session.execute(
            select(KnowledgeBaseVersionORM)
            .where(
                KnowledgeBaseVersionORM.id == version_id,
                KnowledgeBaseVersionORM.knowledge_base_id == knowledge_base_id,
            )
            .with_for_update()
        )
        candidate = result.scalar_one_or_none()
        if (
            candidate is None
            or candidate.state != KnowledgeVersionState.BUILDING.value
            or candidate.published_at is not None
        ):
            return False
        candidate.state = KnowledgeVersionState.FAILED.value
        candidate.metrics = mutable_json_value(metrics or {})
        await self.db_session.flush()
        return True
