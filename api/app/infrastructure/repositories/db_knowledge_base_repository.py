#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.knowledge_base import (
    ChunkLevel,
    DocStatus,
    KBStatus,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEntity,
    KnowledgeRelation,
)
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.infrastructure.models.knowledge_base import (
    KnowledgeBaseModel,
    KnowledgeChunkModel,
    KnowledgeDocumentModel,
    KnowledgeEntityModel,
    KnowledgeRelationModel,
)


class DBKnowledgeBaseRepository(KnowledgeBaseRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    def _apply_scope(self, stmt, scope: Optional[OwnerScope]):
        if scope is None:
            return stmt
        if scope.type == OwnerScopeType.TEAM:
            return stmt.where(KnowledgeBaseModel.team_id == scope.team_id)
        return stmt.where(KnowledgeBaseModel.owner_user_id == scope.user_id, KnowledgeBaseModel.team_id.is_(None))

    async def save_kb(self, kb: KnowledgeBase) -> None:
        stmt = select(KnowledgeBaseModel).where(KnowledgeBaseModel.id == kb.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            self.db_session.add(KnowledgeBaseModel.from_domain(kb))
            return
        record.name = kb.name
        record.status = kb.status.value
        record.doc_count = kb.doc_count
        record.chunk_count = kb.chunk_count
        record.ingest_task_id = kb.ingest_task_id
        record.error = kb.error
        record.vector_degraded = kb.vector_degraded
        record.settings = kb.settings
        record.owner_user_id = kb.owner_user_id
        record.team_id = kb.team_id
        record.updated_at = kb.updated_at

    async def get_kb(self, kb_id: str, scope: Optional[OwnerScope] = None) -> Optional[KnowledgeBase]:
        stmt = self._apply_scope(select(KnowledgeBaseModel).where(KnowledgeBaseModel.id == kb_id), scope)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def list_kbs(self, limit: int = 100, offset: int = 0, scope: Optional[OwnerScope] = None) -> List[KnowledgeBase]:
        stmt = (
            self._apply_scope(select(KnowledgeBaseModel), scope)
            .order_by(KnowledgeBaseModel.updated_at.desc())
            .offset(max(offset, 0))
            .limit(max(1, min(limit, 500)))
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def list_stuck_ingesting(self, limit: int = 100) -> List[KnowledgeBase]:
        non_terminal = {
            KBStatus.PENDING.value,
            KBStatus.PARSING.value,
            KBStatus.CHUNKING.value,
            KBStatus.INDEXING.value,
            KBStatus.GRAPH_BUILDING.value,
        }
        stmt = (
            select(KnowledgeBaseModel)
            .where(KnowledgeBaseModel.status.in_(non_terminal))
            .where(KnowledgeBaseModel.ingest_task_id.is_not(None))
            .order_by(KnowledgeBaseModel.updated_at.asc())
            .limit(max(1, min(limit, 500)))
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def delete_kb(self, kb_id: str) -> None:
        await self.db_session.execute(delete(KnowledgeBaseModel).where(KnowledgeBaseModel.id == kb_id))

    async def update_status(
            self,
            kb_id: str,
            status: KBStatus,
            error: Optional[str] = None,
    ) -> None:
        values = {"status": status.value, "updated_at": datetime.now()}
        if error is not None:
            values["error"] = error
        await self.db_session.execute(
            update(KnowledgeBaseModel).where(KnowledgeBaseModel.id == kb_id).values(**values)
        )

    async def save_document(self, document: KnowledgeDocument) -> None:
        stmt = select(KnowledgeDocumentModel).where(KnowledgeDocumentModel.id == document.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            self.db_session.add(
                KnowledgeDocumentModel(
                    id=document.id,
                    kb_id=document.kb_id,
                    title=document.title,
                    source_type=document.source_type.value,
                    source_ref=document.source_ref,
                    mime=document.mime,
                    file_id=document.file_id,
                    page_count=document.page_count,
                    status=document.status.value,
                    error=document.error,
                    warning=document.warning,
                    created_at=document.created_at,
                    updated_at=document.updated_at,
                )
            )
            return
        record.title = document.title
        record.source_type = document.source_type.value
        record.source_ref = document.source_ref
        record.mime = document.mime
        record.file_id = document.file_id
        record.page_count = document.page_count
        record.status = document.status.value
        record.error = document.error
        record.warning = document.warning
        record.updated_at = document.updated_at

    async def list_documents(self, kb_id: str) -> List[KnowledgeDocument]:
        stmt = (
            select(KnowledgeDocumentModel)
            .where(KnowledgeDocumentModel.kb_id == kb_id)
            .order_by(KnowledgeDocumentModel.created_at.asc())
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def get_document(self, doc_id: str) -> Optional[KnowledgeDocument]:
        stmt = select(KnowledgeDocumentModel).where(KnowledgeDocumentModel.id == doc_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def update_document_status(
            self,
            doc_id: str,
            status: DocStatus,
            error: Optional[str] = None,
            warning: Optional[str] = None,
            page_count: Optional[int] = None,
    ) -> None:
        values = {"status": status.value, "updated_at": datetime.now()}
        if error is not None:
            values["error"] = error
        if warning is not None:
            values["warning"] = warning
        if page_count is not None:
            values["page_count"] = page_count
        await self.db_session.execute(
            update(KnowledgeDocumentModel).where(KnowledgeDocumentModel.id == doc_id).values(**values)
        )

    async def clear_index_data(self, kb_id: str) -> None:
        await self.db_session.execute(
            delete(KnowledgeRelationModel).where(KnowledgeRelationModel.kb_id == kb_id)
        )
        await self.db_session.execute(
            delete(KnowledgeEntityModel).where(KnowledgeEntityModel.kb_id == kb_id)
        )
        await self.db_session.execute(
            delete(KnowledgeChunkModel).where(KnowledgeChunkModel.kb_id == kb_id)
        )

    async def replace_index_chunks(self, kb_id: str, chunks: List[KnowledgeChunk]) -> None:
        await self.clear_index_data(kb_id)
        await self.save_chunks(chunks)

    async def save_chunks(self, chunks: List[KnowledgeChunk]) -> None:
        for chunk in chunks:
            await self._insert_chunk(chunk)

    async def _insert_chunk(self, chunk: KnowledgeChunk) -> None:
            params = {
                "id": chunk.id,
                "kb_id": chunk.kb_id,
                "doc_id": chunk.doc_id,
                "parent_id": chunk.parent_id,
                "level": chunk.level.value,
                "content": chunk.content,
                "segmented_content": chunk.segmented_content or chunk.content,
                "page_no": chunk.page_no,
                "heading_path": chunk.heading_path,
                "ordinal": chunk.ordinal,
                "embedding": str(chunk.embedding),
            }
            if chunk.embedding:
                await self.db_session.execute(
                    text(
                        """
                        INSERT INTO knowledge_chunks
                            (id, kb_id, doc_id, parent_id, level, content, content_tsv,
                             page_no, heading_path, ordinal, embedding)
                        VALUES
                            (:id, :kb_id, :doc_id, :parent_id, :level, :content,
                             to_tsvector('simple', :segmented_content),
                             :page_no, :heading_path, :ordinal, :embedding::vector)
                        """
                    ),
                    params,
                )
            else:
                await self.db_session.execute(
                    text(
                        """
                        INSERT INTO knowledge_chunks
                            (id, kb_id, doc_id, parent_id, level, content, content_tsv,
                             page_no, heading_path, ordinal)
                        VALUES
                            (:id, :kb_id, :doc_id, :parent_id, :level, :content,
                             to_tsvector('simple', :segmented_content),
                             :page_no, :heading_path, :ordinal)
                        """
                    ),
                    params,
                )

    async def vector_search_chunks(
            self,
            kb_id: str,
            query_embedding: List[float],
            limit: int = 20,
    ) -> List[Tuple[KnowledgeChunk, KnowledgeDocument, float]]:
        if not query_embedding:
            return []
        result = await self.db_session.execute(
            text(
                """
                SELECT c.id, c.kb_id, c.doc_id, c.parent_id, c.level, c.content,
                       c.page_no, c.heading_path, c.ordinal,
                       d.title, d.source_type, d.source_ref, d.mime, d.file_id,
                       d.page_count, d.status, d.error, d.warning, d.created_at, d.updated_at,
                       1 - (c.embedding <=> :query::vector) AS score
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.id = c.doc_id
                WHERE c.kb_id = :kb_id AND c.level = 'child' AND c.embedding IS NOT NULL
                ORDER BY c.embedding <=> :query::vector
                LIMIT :limit
                """
            ),
            {"kb_id": kb_id, "query": str(query_embedding), "limit": limit},
        )
        return [self._row_to_chunk_doc_score(row) for row in result.fetchall()]

    async def bm25_search_chunks(
            self,
            kb_id: str,
            segmented_query: str,
            limit: int = 20,
    ) -> List[Tuple[KnowledgeChunk, KnowledgeDocument, float]]:
        if not segmented_query.strip():
            return []
        result = await self.db_session.execute(
            text(
                """
                SELECT c.id, c.kb_id, c.doc_id, c.parent_id, c.level, c.content,
                       c.page_no, c.heading_path, c.ordinal,
                       d.title, d.source_type, d.source_ref, d.mime, d.file_id,
                       d.page_count, d.status, d.error, d.warning, d.created_at, d.updated_at,
                       ts_rank(c.content_tsv, plainto_tsquery('simple', :query)) AS score
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.id = c.doc_id
                WHERE c.kb_id = :kb_id
                  AND c.level = 'child'
                  AND c.content_tsv @@ plainto_tsquery('simple', :query)
                ORDER BY score DESC
                LIMIT :limit
                """
            ),
            {"kb_id": kb_id, "query": segmented_query, "limit": limit},
        )
        return [self._row_to_chunk_doc_score(row) for row in result.fetchall()]

    async def get_parents_by_ids(self, parent_ids: List[str]) -> List[KnowledgeChunk]:
        if not parent_ids:
            return []
        stmt = select(KnowledgeChunkModel).where(KnowledgeChunkModel.id.in_(parent_ids))
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def get_chunks_by_ids(self, chunk_ids: List[str]) -> List[KnowledgeChunk]:
        if not chunk_ids:
            return []
        stmt = select(KnowledgeChunkModel).where(KnowledgeChunkModel.id.in_(chunk_ids))
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def list_chunks_for_document(
            self,
            doc_id: str,
            page_no: Optional[int] = None,
            limit: int = 20,
    ) -> List[KnowledgeChunk]:
        stmt = (
            select(KnowledgeChunkModel)
            .where(KnowledgeChunkModel.doc_id == doc_id)
            .order_by(KnowledgeChunkModel.ordinal.asc())
            .limit(max(1, min(limit, 200)))
        )
        if page_no is not None:
            stmt = stmt.where(KnowledgeChunkModel.page_no == page_no)
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def save_entities(self, entities: List[KnowledgeEntity]) -> None:
        for entity in entities:
            self.db_session.add(
                KnowledgeEntityModel(
                    id=entity.id,
                    kb_id=entity.kb_id,
                    name=entity.name,
                    type=entity.type,
                    description=entity.description,
                )
            )

    async def save_relations(self, relations: List[KnowledgeRelation]) -> None:
        for relation in relations:
            self.db_session.add(
                KnowledgeRelationModel(
                    id=relation.id,
                    kb_id=relation.kb_id,
                    src_entity_id=relation.src_entity_id,
                    dst_entity_id=relation.dst_entity_id,
                    relation=relation.relation,
                    chunk_id=relation.chunk_id,
                )
            )

    async def list_entities(self, kb_id: str, name: Optional[str] = None) -> List[KnowledgeEntity]:
        stmt = select(KnowledgeEntityModel).where(KnowledgeEntityModel.kb_id == kb_id)
        if name:
            stmt = stmt.where(KnowledgeEntityModel.name.ilike(f"%{name}%"))
        stmt = stmt.order_by(KnowledgeEntityModel.name.asc()).limit(100)
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def list_relations_for_entities(
            self,
            kb_id: str,
            entity_ids: List[str],
    ) -> List[KnowledgeRelation]:
        if not entity_ids:
            return []
        stmt = (
            select(KnowledgeRelationModel)
            .where(KnowledgeRelationModel.kb_id == kb_id)
            .where(
                or_(
                    KnowledgeRelationModel.src_entity_id.in_(entity_ids),
                    KnowledgeRelationModel.dst_entity_id.in_(entity_ids),
                )
            )
            .limit(200)
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def get_related_chunk_ids(self, kb_id: str, chunk_ids: List[str], limit: int = 20) -> List[str]:
        if not chunk_ids:
            return []
        seed_result = await self.db_session.execute(
            select(KnowledgeRelationModel)
            .where(KnowledgeRelationModel.kb_id == kb_id)
            .where(KnowledgeRelationModel.chunk_id.in_(chunk_ids))
        )
        entity_ids = set()
        for relation in seed_result.scalars().all():
            entity_ids.add(relation.src_entity_id)
            entity_ids.add(relation.dst_entity_id)
        if not entity_ids:
            return []
        related_result = await self.db_session.execute(
            select(KnowledgeRelationModel.chunk_id)
            .where(KnowledgeRelationModel.kb_id == kb_id)
            .where(KnowledgeRelationModel.chunk_id.is_not(None))
            .where(KnowledgeRelationModel.chunk_id.not_in(chunk_ids))
            .where(
                or_(
                    KnowledgeRelationModel.src_entity_id.in_(entity_ids),
                    KnowledgeRelationModel.dst_entity_id.in_(entity_ids),
                )
            )
            .limit(max(1, min(limit, 100)))
        )
        return [str(chunk_id) for chunk_id in related_result.scalars().all() if chunk_id]

    @staticmethod
    def _row_to_chunk_doc_score(row) -> Tuple[KnowledgeChunk, KnowledgeDocument, float]:
        chunk = KnowledgeChunk(
            id=row.id,
            kb_id=row.kb_id,
            doc_id=row.doc_id,
            parent_id=row.parent_id,
            level=ChunkLevel(row.level),
            content=row.content or "",
            page_no=row.page_no,
            heading_path=row.heading_path or "",
            ordinal=row.ordinal or 0,
        )
        doc = KnowledgeDocument(
            id=row.doc_id,
            kb_id=row.kb_id,
            title=row.title,
            source_type=row.source_type,
            source_ref=row.source_ref or "",
            mime=row.mime or "",
            file_id=row.file_id,
            page_count=row.page_count or 0,
            status=row.status,
            error=row.error,
            warning=row.warning,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        return chunk, doc, float(row.score or 0.0)
