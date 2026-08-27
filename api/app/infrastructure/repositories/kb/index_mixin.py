"""Chunk/index writes, candidate metrics, counts, purge, and pagination."""

from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text, update

from app.domain.models.knowledge_base import (
    ChunkLevel,
    DocStatus,
    KnowledgeChunk,
    KnowledgeDocument,
)
from app.domain.models.knowledge_version import KnowledgeVersionState
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
    KnowledgeVersionDocumentORM,
)
from app.infrastructure.repositories.kb._shared import (
    _CHUNK_INSERT_BATCH_SIZE,
)

_CHUNK_INSERT_WITH_EMBEDDING_SQL = text(
    """
    INSERT INTO knowledge_chunks
        (id, kb_id, doc_id, version_id, parent_id, level, content, content_tsv,
         page_no, heading_path, ordinal, embedding)
    VALUES
        (:id, :kb_id, :doc_id, :version_id, :parent_id, :level, :content,
         COALESCE(
           CAST(:content_tsv AS tsvector),
           to_tsvector('simple', :segmented_content)
         ),
         :page_no, :heading_path, :ordinal, CAST(:embedding AS vector))
    """
)


_CHUNK_INSERT_PLAIN_SQL = text(
    """
    INSERT INTO knowledge_chunks
        (id, kb_id, doc_id, version_id, parent_id, level, content, content_tsv,
         page_no, heading_path, ordinal)
    VALUES
        (:id, :kb_id, :doc_id, :version_id, :parent_id, :level, :content,
         COALESCE(
           CAST(:content_tsv AS tsvector),
           to_tsvector('simple', :segmented_content)
         ),
         :page_no, :heading_path, :ordinal)
    """
)


class KBIndexMixin:
    """Chunk/index write path + candidate metrics + document
    count/purge/pagination methods for DBKnowledgeBaseRepository."""

    async def clear_index_data(self, kb_id: str) -> None:
        await self.db_session.execute(
            delete(KnowledgeRelationModel).where(KnowledgeRelationModel.kb_id == kb_id)
        )
        await self.db_session.execute(
            delete(KnowledgeEntityRefModel).where(KnowledgeEntityRefModel.kb_id == kb_id)
        )
        await self.db_session.execute(
            delete(KnowledgeEntityModel).where(KnowledgeEntityModel.kb_id == kb_id)
        )
        await self.db_session.execute(
            delete(KnowledgeChunkModel).where(KnowledgeChunkModel.kb_id == kb_id)
        )

    async def replace_index_chunks(self, kb_id: str, chunks: list[KnowledgeChunk]) -> None:
        await self.clear_index_data(kb_id)
        await self.save_chunks(chunks)

    async def save_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        embedded = [c for c in chunks if c.embedding]
        plain = [c for c in chunks if not c.embedding]
        for batch_source, sql in (
            (embedded, _CHUNK_INSERT_WITH_EMBEDDING_SQL),
            (plain, _CHUNK_INSERT_PLAIN_SQL),
        ):
            for start in range(0, len(batch_source), _CHUNK_INSERT_BATCH_SIZE):
                batch = batch_source[start : start + _CHUNK_INSERT_BATCH_SIZE]
                await self.db_session.execute(sql, [self._chunk_params(chunk) for chunk in batch])

    async def replace_candidate_chunks(
        self,
        kb_id: str,
        version_id: str,
        chunks: list[KnowledgeChunk],
    ) -> None:
        await self._require_building_candidate(kb_id, version_id)
        if any(chunk.kb_id != kb_id or chunk.version_id != version_id for chunk in chunks):
            raise ValueError("candidate chunks must carry exact version ownership")
        await self._delete_candidate_graph(version_id)
        await self.db_session.execute(
            delete(KnowledgeChunkModel).where(
                KnowledgeChunkModel.kb_id == kb_id,
                KnowledgeChunkModel.version_id == version_id,
            )
        )
        await self.save_chunks(chunks)

    async def get_candidate_index_metrics(
        self,
        kb_id: str,
        version_id: str,
    ) -> dict[str, int]:
        await self._require_building_candidate(kb_id, version_id)
        manifest_total = await self._count(
            KnowledgeVersionDocumentORM,
            KnowledgeVersionDocumentORM.knowledge_base_id == kb_id,
            KnowledgeVersionDocumentORM.version_id == version_id,
        )
        manifest_indexed = await self._count(
            KnowledgeVersionDocumentORM,
            KnowledgeVersionDocumentORM.knowledge_base_id == kb_id,
            KnowledgeVersionDocumentORM.version_id == version_id,
            KnowledgeVersionDocumentORM.state == "indexed",
        )
        manifest_failed = await self._count(
            KnowledgeVersionDocumentORM,
            KnowledgeVersionDocumentORM.knowledge_base_id == kb_id,
            KnowledgeVersionDocumentORM.version_id == version_id,
            KnowledgeVersionDocumentORM.state == "failed",
        )
        parent_chunks = await self._count(
            KnowledgeChunkModel,
            KnowledgeChunkModel.kb_id == kb_id,
            KnowledgeChunkModel.version_id == version_id,
            KnowledgeChunkModel.level == ChunkLevel.PARENT.value,
        )
        child_chunks = await self._count(
            KnowledgeChunkModel,
            KnowledgeChunkModel.kb_id == kb_id,
            KnowledgeChunkModel.version_id == version_id,
            KnowledgeChunkModel.level == ChunkLevel.CHILD.value,
        )
        entities = await self._count(
            KnowledgeEntityModel,
            KnowledgeEntityModel.kb_id == kb_id,
            KnowledgeEntityModel.version_id == version_id,
        )
        relations = await self._count(
            KnowledgeRelationModel,
            KnowledgeRelationModel.kb_id == kb_id,
            KnowledgeRelationModel.version_id == version_id,
        )
        refs = await self._count(
            KnowledgeEntityRefModel,
            KnowledgeEntityRefModel.kb_id == kb_id,
            KnowledgeEntityRefModel.version_id == version_id,
        )
        orphan_chunks = int(
            (
                await self.db_session.execute(
                    select(func.count())
                    .select_from(KnowledgeChunkModel)
                    .outerjoin(
                        KnowledgeVersionDocumentORM,
                        (KnowledgeVersionDocumentORM.version_id == KnowledgeChunkModel.version_id)
                        & (KnowledgeVersionDocumentORM.document_id == KnowledgeChunkModel.doc_id),
                    )
                    .where(
                        KnowledgeChunkModel.version_id == version_id,
                        KnowledgeChunkModel.kb_id == kb_id,
                        KnowledgeVersionDocumentORM.document_id.is_(None),
                    )
                )
            ).scalar_one()
        )
        if orphan_chunks:
            raise ValueError("candidate chunk manifest closure is incomplete")
        closure_checks = {
            "candidate manifest revision closure is incomplete": """
                SELECT count(*)
                FROM knowledge_base_version_documents manifest
                LEFT JOIN knowledge_document_revisions revision
                  ON revision.id = manifest.document_revision_id
                 AND revision.document_id = manifest.document_id
                LEFT JOIN knowledge_documents document
                  ON document.id = revision.document_id
                 AND document.kb_id = manifest.knowledge_base_id
                WHERE manifest.version_id = :version_id
                  AND manifest.knowledge_base_id = :kb_id
                  AND (
                    revision.id IS NULL
                    OR document.id IS NULL
                  )
            """,
            "candidate manifest revision state mismatch": """
                SELECT count(*)
                FROM knowledge_base_version_documents manifest
                JOIN knowledge_document_revisions revision
                  ON revision.id = manifest.document_revision_id
                 AND revision.document_id = manifest.document_id
                WHERE manifest.version_id = :version_id
                  AND manifest.knowledge_base_id = :kb_id
                  AND manifest.state <> revision.state
            """,
            "candidate indexed manifest requires child chunks": """
                SELECT count(*)
                FROM (
                  SELECT manifest.version_id, manifest.document_id
                  FROM knowledge_base_version_documents manifest
                  LEFT JOIN knowledge_chunks child
                    ON child.version_id = manifest.version_id
                   AND child.doc_id = manifest.document_id
                   AND child.level = 'child'
                  WHERE manifest.version_id = :version_id
                    AND manifest.knowledge_base_id = :kb_id
                    AND manifest.state = 'indexed'
                  GROUP BY manifest.version_id, manifest.document_id
                  HAVING count(child.id) = 0
                ) missing_indexed_children
            """,
            "candidate failed manifest cannot own chunks": """
                SELECT count(*)
                FROM knowledge_base_version_documents manifest
                JOIN knowledge_chunks chunk
                  ON chunk.version_id = manifest.version_id
                 AND chunk.doc_id = manifest.document_id
                WHERE manifest.version_id = :version_id
                  AND manifest.knowledge_base_id = :kb_id
                  AND manifest.state = 'failed'
            """,
            "candidate child-parent closure is incomplete": """
                SELECT count(*)
                FROM knowledge_chunks child
                LEFT JOIN knowledge_chunks parent
                  ON parent.id = child.parent_id
                 AND parent.version_id = child.version_id
                WHERE child.version_id = :version_id
                  AND child.kb_id = :kb_id
                  AND child.level = 'child'
                  AND (
                    child.parent_id IS NULL
                    OR parent.id IS NULL
                    OR parent.level <> 'parent'
                    OR parent.kb_id <> child.kb_id
                    OR parent.doc_id <> child.doc_id
                  )
            """,
            "candidate relation closure is incomplete": """
                SELECT count(*)
                FROM knowledge_relations relation
                LEFT JOIN knowledge_entities src
                  ON src.id = relation.src_entity_id
                 AND src.version_id = relation.version_id
                LEFT JOIN knowledge_entities dst
                  ON dst.id = relation.dst_entity_id
                 AND dst.version_id = relation.version_id
                LEFT JOIN knowledge_chunks chunk
                  ON chunk.id = relation.chunk_id
                 AND chunk.version_id = relation.version_id
                WHERE relation.version_id = :version_id
                  AND relation.kb_id = :kb_id
                  AND (
                    src.id IS NULL
                    OR dst.id IS NULL
                    OR src.kb_id <> relation.kb_id
                    OR dst.kb_id <> relation.kb_id
                    OR (
                      relation.chunk_id IS NOT NULL
                      AND (
                        chunk.id IS NULL
                        OR chunk.kb_id <> relation.kb_id
                      )
                    )
                  )
            """,
            "candidate entity-ref closure is incomplete": """
                SELECT count(*)
                FROM knowledge_entity_refs ref
                LEFT JOIN knowledge_entities entity
                  ON entity.id = ref.entity_id
                 AND entity.version_id = ref.version_id
                LEFT JOIN knowledge_base_version_documents manifest
                  ON manifest.version_id = ref.version_id
                 AND manifest.document_id = ref.doc_id
                WHERE ref.version_id = :version_id
                  AND ref.kb_id = :kb_id
                  AND (
                    entity.id IS NULL
                    OR manifest.document_id IS NULL
                    OR entity.kb_id <> ref.kb_id
                    OR manifest.knowledge_base_id <> ref.kb_id
                  )
            """,
            "candidate failed manifest cannot own graph evidence": """
                SELECT count(*)
                FROM knowledge_base_version_documents manifest
                LEFT JOIN knowledge_entity_refs ref
                  ON ref.version_id = manifest.version_id
                 AND ref.kb_id = manifest.knowledge_base_id
                 AND ref.doc_id = manifest.document_id
                LEFT JOIN knowledge_chunks chunk
                  ON chunk.version_id = manifest.version_id
                 AND chunk.kb_id = manifest.knowledge_base_id
                 AND chunk.doc_id = manifest.document_id
                LEFT JOIN knowledge_relations relation
                  ON relation.version_id = manifest.version_id
                 AND relation.kb_id = manifest.knowledge_base_id
                 AND relation.chunk_id = chunk.id
                WHERE manifest.version_id = :version_id
                  AND manifest.knowledge_base_id = :kb_id
                  AND manifest.state = 'failed'
                  AND (
                    ref.id IS NOT NULL
                    OR relation.id IS NOT NULL
                  )
            """,
        }
        for message, sql in closure_checks.items():
            result = await self.db_session.execute(
                text(sql),
                {"kb_id": kb_id, "version_id": version_id},
            )
            if int(result.scalar_one()):
                raise ValueError(message)
        vector_result = await self.db_session.execute(
            text(
                """
                SELECT count(*)
                FROM knowledge_chunks
                WHERE kb_id = :kb_id
                  AND version_id = :version_id
                  AND level = 'child'
                  AND embedding IS NOT NULL
                """
            ),
            {"kb_id": kb_id, "version_id": version_id},
        )
        vector_chunks = int(vector_result.scalar_one())
        return {
            "document_count": manifest_total,
            "indexed_document_count": manifest_indexed,
            "failed_document_count": manifest_failed,
            "parent_chunk_count": parent_chunks,
            "child_chunk_count": child_chunks,
            "vector_chunk_count": vector_chunks,
            "entity_count": entities,
            "relation_count": relations,
            "entity_ref_count": refs,
        }

    async def _count(self, model, *predicates) -> int:
        result = await self.db_session.execute(
            select(func.count()).select_from(model).where(*predicates)
        )
        return int(result.scalar_one())

    async def _require_building_candidate(
        self,
        kb_id: str,
        version_id: str,
    ) -> None:
        result = await self.db_session.execute(
            select(KnowledgeBaseVersionORM.id).where(
                KnowledgeBaseVersionORM.id == version_id,
                KnowledgeBaseVersionORM.knowledge_base_id == kb_id,
                KnowledgeBaseVersionORM.state == KnowledgeVersionState.BUILDING.value,
                KnowledgeBaseVersionORM.published_at.is_(None),
            )
        )
        if result.scalar_one_or_none() is None:
            raise ValueError("candidate-scoped write requires a building version")

    @staticmethod
    def _chunk_params(chunk: KnowledgeChunk) -> dict:
        return {
            "id": chunk.id,
            "kb_id": chunk.kb_id,
            "doc_id": chunk.doc_id,
            "version_id": chunk.version_id,
            "parent_id": chunk.parent_id,
            "level": chunk.level.value,
            "content": chunk.content,
            "segmented_content": chunk.segmented_content or chunk.content,
            "content_tsv": chunk.content_tsv,
            "page_no": chunk.page_no,
            "heading_path": chunk.heading_path,
            "ordinal": chunk.ordinal,
            "embedding": str(chunk.embedding),
        }

    async def purge_documents_index_data(self, doc_ids: list[str]) -> None:
        if not doc_ids:
            return
        chunk_ids_stmt = select(KnowledgeChunkModel.id).where(
            KnowledgeChunkModel.doc_id.in_(doc_ids)
        )
        await self.db_session.execute(
            delete(KnowledgeRelationModel).where(
                KnowledgeRelationModel.chunk_id.in_(chunk_ids_stmt)
            )
        )
        candidate_result = await self.db_session.execute(
            select(KnowledgeEntityRefModel.entity_id)
            .where(KnowledgeEntityRefModel.doc_id.in_(doc_ids))
            .distinct()
        )
        candidates = [str(entity_id) for entity_id in candidate_result.scalars().all()]
        await self.db_session.execute(
            delete(KnowledgeEntityRefModel).where(KnowledgeEntityRefModel.doc_id.in_(doc_ids))
        )
        if candidates:
            remaining_refs = select(KnowledgeEntityRefModel.entity_id).where(
                KnowledgeEntityRefModel.entity_id == KnowledgeEntityModel.id
            )
            await self.db_session.execute(
                delete(KnowledgeEntityModel)
                .where(KnowledgeEntityModel.id.in_(candidates))
                .where(~remaining_refs.exists())
            )
        await self.db_session.execute(
            delete(KnowledgeChunkModel).where(KnowledgeChunkModel.doc_id.in_(doc_ids))
        )

    async def count_ready_documents(self, kb_ids: list[str]) -> dict[str, int]:
        if not kb_ids:
            return {}
        stmt = (
            select(KnowledgeDocumentModel.kb_id, func.count())
            .join(
                KnowledgeBaseModel,
                KnowledgeBaseModel.id == KnowledgeDocumentModel.kb_id,
            )
            .join(
                KnowledgeBaseVersionORM,
                self._active_version_join_predicate(),
            )
            .outerjoin(
                KnowledgeVersionDocumentORM,
                (KnowledgeVersionDocumentORM.document_id == KnowledgeDocumentModel.id)
                & (KnowledgeVersionDocumentORM.knowledge_base_id == KnowledgeDocumentModel.kb_id)
                & (KnowledgeVersionDocumentORM.version_id == KnowledgeBaseModel.active_version_id),
            )
            .where(KnowledgeDocumentModel.kb_id.in_(kb_ids))
            .where(self._active_document_predicate())
            .where(KnowledgeVersionDocumentORM.state == "indexed")
            .group_by(KnowledgeDocumentModel.kb_id)
        )
        result = await self.db_session.execute(stmt)
        counts = dict.fromkeys(kb_ids, 0)
        for kb_id, count in result.all():
            counts[str(kb_id)] = int(count)
        return counts

    async def count_child_chunks(self, kb_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(KnowledgeChunkModel)
            .join(
                KnowledgeBaseModel,
                KnowledgeBaseModel.id == KnowledgeChunkModel.kb_id,
            )
            .join(
                KnowledgeBaseVersionORM,
                self._active_version_join_predicate(),
            )
            .where(KnowledgeChunkModel.kb_id == kb_id)
            .where(self._active_version_row_predicate(KnowledgeChunkModel.version_id))
            .where(KnowledgeChunkModel.level == ChunkLevel.CHILD.value)
        )
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one())

    async def list_documents_page(
        self,
        kb_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[KnowledgeDocument], int]:
        stmt = (
            select(KnowledgeDocumentModel)
            .join(
                KnowledgeBaseModel,
                KnowledgeBaseModel.id == KnowledgeDocumentModel.kb_id,
            )
            .join(
                KnowledgeBaseVersionORM,
                self._active_version_join_predicate(),
            )
            .outerjoin(
                KnowledgeVersionDocumentORM,
                (KnowledgeVersionDocumentORM.document_id == KnowledgeDocumentModel.id)
                & (KnowledgeVersionDocumentORM.knowledge_base_id == KnowledgeDocumentModel.kb_id)
                & (KnowledgeVersionDocumentORM.version_id == KnowledgeBaseModel.active_version_id),
            )
            .where(KnowledgeDocumentModel.kb_id == kb_id)
            .where(self._active_document_predicate())
            .order_by(KnowledgeDocumentModel.created_at.asc())
            .offset(max(offset, 0))
            .limit(max(1, min(limit, 200)))
        )
        result = await self.db_session.execute(stmt)
        items = [record.to_domain() for record in result.scalars().all()]
        total = await self.count_documents(kb_id)
        return items, total

    async def mark_documents_pending(self, kb_id: str) -> None:
        await self.db_session.execute(
            update(KnowledgeDocumentModel)
            .where(KnowledgeDocumentModel.kb_id == kb_id)
            .values(status=DocStatus.PENDING.value, error=None, updated_at=datetime.now(UTC))
        )
