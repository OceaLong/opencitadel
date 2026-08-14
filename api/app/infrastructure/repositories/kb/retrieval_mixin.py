#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""KBRetrievalMixin: vector/BM25 search, versioned retrieval, and
non-versioned chunk/document read methods for DBKnowledgeBaseRepository.

Pure re-homed methods split out of db_knowledge_base_repository.py
(Phase C task 5, KB repository mixin split).
"""
import math
from typing import List, Optional, Tuple

from sqlalchemy import and_, func, or_, select, text

from app.domain.models.knowledge_base import (
    ChunkLevel,
    KnowledgeChunk,
    KnowledgeDocument,
)
from app.domain.repositories.knowledge_base_repository import (
    DocumentPage,
    DocumentPageItem,
    KNOWLEDGE_EMBEDDING_DIMENSION,
    VersionedKnowledgeChunk,
)
from app.infrastructure.models.knowledge_base import (
    KnowledgeBaseModel,
    KnowledgeChunkModel,
    KnowledgeDocumentModel,
)
from app.infrastructure.models.knowledge_version import (
    KnowledgeBaseVersionORM,
    KnowledgeDocumentRevisionORM,
    KnowledgeVersionDocumentORM,
)
from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeVersionState,
)
from app.infrastructure.repositories.kb._shared import (
    _decode_document_cursor,
    _encode_document_cursor,
    _is_cursor_int,
    build_versioned_vector_search_statement,
)


class KBRetrievalMixin:
    """Vector/BM25 search + versioned retrieval + non-versioned
    chunk/document read methods for DBKnowledgeBaseRepository."""

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
                SELECT c.id, c.kb_id, c.doc_id, c.version_id,
                       c.parent_id, c.level, c.content,
                       c.page_no, c.heading_path, c.ordinal,
                       d.title, d.source_type, d.source_ref, d.mime, d.file_id,
                       d.page_count, d.status, d.error, d.warning, d.created_at, d.updated_at,
                       1 - (c.embedding <=> :query::vector) AS score
                FROM knowledge_chunks c
                JOIN knowledge_bases kb
                  ON kb.id = c.kb_id
                JOIN knowledge_base_versions active
                  ON active.id = kb.active_version_id
                 AND active.knowledge_base_id = kb.id
                JOIN knowledge_documents d ON d.id = c.doc_id
                WHERE c.kb_id = :kb_id
                  AND (
                    c.version_id = kb.active_version_id
                    OR (
                      c.version_id IS NULL
                      AND active.legacy_snapshot IS TRUE
                    )
                  )
                  AND c.level = 'child'
                  AND c.embedding IS NOT NULL
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
                SELECT c.id, c.kb_id, c.doc_id, c.version_id,
                       c.parent_id, c.level, c.content,
                       c.page_no, c.heading_path, c.ordinal,
                       d.title, d.source_type, d.source_ref, d.mime, d.file_id,
                       d.page_count, d.status, d.error, d.warning, d.created_at, d.updated_at,
                       ts_rank(c.content_tsv, plainto_tsquery('simple', :query)) AS score
                FROM knowledge_chunks c
                JOIN knowledge_bases kb
                  ON kb.id = c.kb_id
                JOIN knowledge_base_versions active
                  ON active.id = kb.active_version_id
                 AND active.knowledge_base_id = kb.id
                JOIN knowledge_documents d ON d.id = c.doc_id
                WHERE c.kb_id = :kb_id
                  AND (
                    c.version_id = kb.active_version_id
                    OR (
                      c.version_id IS NULL
                      AND active.legacy_snapshot IS TRUE
                    )
                  )
                  AND c.level = 'child'
                  AND c.content_tsv @@ plainto_tsquery('simple', :query)
                ORDER BY score DESC
                LIMIT :limit
                """
            ),
            {"kb_id": kb_id, "query": segmented_query, "limit": limit},
        )
        return [self._row_to_chunk_doc_score(row) for row in result.fetchall()]

    async def vector_search_chunks_for_version(
            self,
            kb_id: str,
            version_id: str,
            query_embedding: List[float],
            limit: int = 20,
    ) -> List[VersionedKnowledgeChunk]:
        if not self._valid_embedding(query_embedding):
            raise ValueError("query embedding is empty or malformed")
        try:
            await self.db_session.execute(
                text("SET LOCAL hnsw.iterative_scan = 'strict_order'")
            )
        except Exception as exc:
            raise RuntimeError(
                "pgvector filtered ANN requires hnsw.iterative_scan "
                "support (pgvector 0.8.0+)"
            ) from exc
        result = await self.db_session.execute(
            build_versioned_vector_search_statement(),
            {
                "kb_id": kb_id,
                "version_id": version_id,
                "query": str([float(item) for item in query_embedding]),
                "limit": max(1, min(limit, 200)),
            },
        )
        return [
            self._row_to_versioned_chunk(row)
            for row in result.fetchall()
        ]

    async def bm25_search_chunks_for_version(
            self,
            kb_id: str,
            version_id: str,
            segmented_query: str,
            limit: int = 20,
    ) -> List[VersionedKnowledgeChunk]:
        if not segmented_query.strip():
            return []
        result = await self.db_session.execute(
            text(
                """
                SELECT c.id, c.kb_id, c.doc_id, c.version_id,
                       c.parent_id, c.level, c.content,
                       c.page_no, c.heading_path, c.ordinal,
                       d.title, d.source_type, d.source_ref, d.mime, d.file_id,
                       d.page_count, d.status, d.error, d.warning,
                       d.created_at, d.updated_at,
                       manifest.document_revision_id,
                       ts_rank(
                         c.content_tsv,
                         plainto_tsquery('simple', :query)
                       ) AS score
                FROM knowledge_chunks c
                JOIN knowledge_base_versions version
                  ON version.id = c.version_id
                 AND version.knowledge_base_id = c.kb_id
                 AND version.state IN ('ready', 'degraded')
                 AND version.published_at IS NOT NULL
                JOIN knowledge_base_version_documents manifest
                  ON manifest.version_id = c.version_id
                 AND manifest.knowledge_base_id = c.kb_id
                 AND manifest.document_id = c.doc_id
                 AND manifest.state = 'indexed'
                JOIN knowledge_document_revisions revision
                  ON revision.id = manifest.document_revision_id
                 AND revision.document_id = manifest.document_id
                 AND revision.state = 'indexed'
                JOIN knowledge_documents d
                  ON d.id = c.doc_id
                 AND d.kb_id = c.kb_id
                WHERE c.kb_id = :kb_id
                  AND c.version_id = :version_id
                  AND c.level = 'child'
                  AND c.content_tsv
                      @@ plainto_tsquery('simple', :query)
                ORDER BY score DESC, c.ordinal ASC, c.id ASC
                LIMIT :limit
                """
            ),
            {
                "kb_id": kb_id,
                "version_id": version_id,
                "query": segmented_query,
                "limit": max(1, min(limit, 200)),
            },
        )
        return [
            self._row_to_versioned_chunk(row)
            for row in result.fetchall()
        ]

    async def get_parents_by_ids_for_version(
            self,
            kb_id: str,
            version_id: str,
            parent_ids: List[str],
    ) -> List[KnowledgeChunk]:
        if not parent_ids:
            return []
        stmt = (
            select(KnowledgeChunkModel)
            .join(
                KnowledgeBaseVersionORM,
                (
                    KnowledgeBaseVersionORM.id
                    == KnowledgeChunkModel.version_id
                )
                & (
                    KnowledgeBaseVersionORM.knowledge_base_id
                    == KnowledgeChunkModel.kb_id
                ),
            )
            .join(
                KnowledgeVersionDocumentORM,
                (
                    KnowledgeVersionDocumentORM.version_id
                    == KnowledgeChunkModel.version_id
                )
                & (
                    KnowledgeVersionDocumentORM.knowledge_base_id
                    == KnowledgeChunkModel.kb_id
                )
                & (
                    KnowledgeVersionDocumentORM.document_id
                    == KnowledgeChunkModel.doc_id
                ),
            )
            .join(
                KnowledgeDocumentRevisionORM,
                (
                    KnowledgeDocumentRevisionORM.id
                    == KnowledgeVersionDocumentORM.document_revision_id
                )
                & (
                    KnowledgeDocumentRevisionORM.document_id
                    == KnowledgeVersionDocumentORM.document_id
                ),
            )
            .where(
                KnowledgeChunkModel.kb_id == kb_id,
                KnowledgeChunkModel.version_id == version_id,
                KnowledgeChunkModel.id.in_(parent_ids),
                KnowledgeChunkModel.level == ChunkLevel.PARENT.value,
                KnowledgeBaseVersionORM.state.in_(
                    (
                        KnowledgeVersionState.READY.value,
                        KnowledgeVersionState.DEGRADED.value,
                    )
                ),
                KnowledgeBaseVersionORM.published_at.is_not(None),
                KnowledgeVersionDocumentORM.state
                == DocumentRevisionState.INDEXED.value,
                KnowledgeDocumentRevisionORM.state
                == DocumentRevisionState.INDEXED.value,
            )
            .order_by(
                KnowledgeChunkModel.ordinal.asc(),
                KnowledgeChunkModel.id.asc(),
            )
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def get_chunks_by_ids_for_version(
            self,
            kb_id: str,
            version_id: str,
            chunk_ids: List[str],
    ) -> List[VersionedKnowledgeChunk]:
        if not chunk_ids:
            return []
        result = await self.db_session.execute(
            text(
                """
                SELECT c.id, c.kb_id, c.doc_id, c.version_id,
                       c.parent_id, c.level, c.content,
                       c.page_no, c.heading_path, c.ordinal,
                       d.title, d.source_type, d.source_ref, d.mime, d.file_id,
                       d.page_count, d.status, d.error, d.warning,
                       d.created_at, d.updated_at,
                       manifest.document_revision_id,
                       0.0 AS score
                FROM knowledge_chunks c
                JOIN knowledge_base_versions version
                  ON version.id = c.version_id
                 AND version.knowledge_base_id = c.kb_id
                 AND version.state IN ('ready', 'degraded')
                 AND version.published_at IS NOT NULL
                JOIN knowledge_base_version_documents manifest
                  ON manifest.version_id = c.version_id
                 AND manifest.knowledge_base_id = c.kb_id
                 AND manifest.document_id = c.doc_id
                 AND manifest.state = 'indexed'
                JOIN knowledge_document_revisions revision
                  ON revision.id = manifest.document_revision_id
                 AND revision.document_id = manifest.document_id
                 AND revision.state = 'indexed'
                JOIN knowledge_documents d
                  ON d.id = c.doc_id
                 AND d.kb_id = c.kb_id
                WHERE c.kb_id = :kb_id
                  AND c.version_id = :version_id
                  AND c.id = ANY(:chunk_ids)
                ORDER BY c.ordinal ASC, c.id ASC
                """
            ),
            {
                "kb_id": kb_id,
                "version_id": version_id,
                "chunk_ids": list(dict.fromkeys(chunk_ids)),
            },
        )
        return [
            self._row_to_versioned_chunk(row)
            for row in result.fetchall()
        ]

    async def get_document_for_version(
            self,
            kb_id: str,
            version_id: str,
            doc_id: str,
    ) -> Optional[Tuple[KnowledgeDocument, str]]:
        result = await self.db_session.execute(
            select(
                KnowledgeDocumentModel,
                KnowledgeVersionDocumentORM.document_revision_id,
            )
            .join(
                KnowledgeVersionDocumentORM,
                (
                    KnowledgeVersionDocumentORM.document_id
                    == KnowledgeDocumentModel.id
                )
                & (
                    KnowledgeVersionDocumentORM.knowledge_base_id
                    == KnowledgeDocumentModel.kb_id
                ),
            )
            .join(
                KnowledgeBaseVersionORM,
                (
                    KnowledgeBaseVersionORM.id
                    == KnowledgeVersionDocumentORM.version_id
                )
                & (
                    KnowledgeBaseVersionORM.knowledge_base_id
                    == KnowledgeVersionDocumentORM.knowledge_base_id
                ),
            )
            .join(
                KnowledgeDocumentRevisionORM,
                (
                    KnowledgeDocumentRevisionORM.id
                    == KnowledgeVersionDocumentORM.document_revision_id
                )
                & (
                    KnowledgeDocumentRevisionORM.document_id
                    == KnowledgeVersionDocumentORM.document_id
                ),
            )
            .where(
                KnowledgeDocumentModel.id == doc_id,
                KnowledgeDocumentModel.kb_id == kb_id,
                KnowledgeVersionDocumentORM.version_id == version_id,
                KnowledgeVersionDocumentORM.state
                == DocumentRevisionState.INDEXED.value,
                KnowledgeDocumentRevisionORM.state
                == DocumentRevisionState.INDEXED.value,
                KnowledgeBaseVersionORM.state.in_(
                    (
                        KnowledgeVersionState.READY.value,
                        KnowledgeVersionState.DEGRADED.value,
                    )
                ),
                KnowledgeBaseVersionORM.published_at.is_not(None),
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        document, revision_id = row
        return document.to_domain(), str(revision_id)

    async def list_chunks_for_document_for_version(
            self,
            kb_id: str,
            version_id: str,
            doc_id: str,
            page_no: Optional[int] = None,
            limit: int = 20,
    ) -> List[KnowledgeChunk]:
        stmt = (
            select(KnowledgeChunkModel)
            .join(
                KnowledgeBaseVersionORM,
                (
                    KnowledgeBaseVersionORM.id
                    == KnowledgeChunkModel.version_id
                )
                & (
                    KnowledgeBaseVersionORM.knowledge_base_id
                    == KnowledgeChunkModel.kb_id
                ),
            )
            .join(
                KnowledgeVersionDocumentORM,
                (
                    KnowledgeVersionDocumentORM.version_id
                    == KnowledgeChunkModel.version_id
                )
                & (
                    KnowledgeVersionDocumentORM.knowledge_base_id
                    == KnowledgeChunkModel.kb_id
                )
                & (
                    KnowledgeVersionDocumentORM.document_id
                    == KnowledgeChunkModel.doc_id
                ),
            )
            .join(
                KnowledgeDocumentRevisionORM,
                (
                    KnowledgeDocumentRevisionORM.id
                    == KnowledgeVersionDocumentORM.document_revision_id
                )
                & (
                    KnowledgeDocumentRevisionORM.document_id
                    == KnowledgeVersionDocumentORM.document_id
                ),
            )
            .where(
                KnowledgeChunkModel.kb_id == kb_id,
                KnowledgeChunkModel.version_id == version_id,
                KnowledgeChunkModel.doc_id == doc_id,
                KnowledgeBaseVersionORM.state.in_(
                    (
                        KnowledgeVersionState.READY.value,
                        KnowledgeVersionState.DEGRADED.value,
                    )
                ),
                KnowledgeBaseVersionORM.published_at.is_not(None),
                KnowledgeVersionDocumentORM.state
                == DocumentRevisionState.INDEXED.value,
                KnowledgeDocumentRevisionORM.state
                == DocumentRevisionState.INDEXED.value,
            )
            .order_by(
                KnowledgeChunkModel.ordinal.asc(),
                KnowledgeChunkModel.id.asc(),
            )
            .limit(max(1, min(limit, 200)))
        )
        if page_no is not None:
            stmt = stmt.where(KnowledgeChunkModel.page_no == page_no)
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def read_document_page_for_version(
            self,
            kb_id: str,
            version_id: str,
            doc_id: str,
            document_revision_id: str,
            *,
            page_no: Optional[int] = None,
            cursor: Optional[str] = None,
            limit: int = 30,
    ) -> DocumentPage:
        if limit < 1 or limit > 200:
            raise ValueError(
                "document page limit must be between 1 and 200"
            )
        if page_no is not None and (
            not _is_cursor_int(page_no) or page_no < 1
        ):
            raise ValueError("document page number must be at least 1")
        cursor_key = (
            _decode_document_cursor(
                cursor,
                kb_id=kb_id,
                version_id=version_id,
                doc_id=doc_id,
                document_revision_id=document_revision_id,
                page_no=page_no,
            )
            if cursor is not None
            else None
        )
        base_stmt = (
            select(KnowledgeChunkModel)
            .join(
                KnowledgeBaseVersionORM,
                (
                    KnowledgeBaseVersionORM.id
                    == KnowledgeChunkModel.version_id
                )
                & (
                    KnowledgeBaseVersionORM.knowledge_base_id
                    == KnowledgeChunkModel.kb_id
                ),
            )
            .join(
                KnowledgeVersionDocumentORM,
                (
                    KnowledgeVersionDocumentORM.version_id
                    == KnowledgeChunkModel.version_id
                )
                & (
                    KnowledgeVersionDocumentORM.knowledge_base_id
                    == KnowledgeChunkModel.kb_id
                )
                & (
                    KnowledgeVersionDocumentORM.document_id
                    == KnowledgeChunkModel.doc_id
                ),
            )
            .join(
                KnowledgeDocumentRevisionORM,
                (
                    KnowledgeDocumentRevisionORM.id
                    == KnowledgeVersionDocumentORM.document_revision_id
                )
                & (
                    KnowledgeDocumentRevisionORM.document_id
                    == KnowledgeVersionDocumentORM.document_id
                ),
            )
            .where(
                KnowledgeChunkModel.kb_id == kb_id,
                KnowledgeChunkModel.version_id == version_id,
                KnowledgeChunkModel.doc_id == doc_id,
                KnowledgeChunkModel.level == ChunkLevel.PARENT.value,
                KnowledgeVersionDocumentORM.document_revision_id
                == document_revision_id,
                KnowledgeVersionDocumentORM.state
                == DocumentRevisionState.INDEXED.value,
                KnowledgeDocumentRevisionORM.id
                == document_revision_id,
                KnowledgeDocumentRevisionORM.state
                == DocumentRevisionState.INDEXED.value,
                KnowledgeBaseVersionORM.state.in_(
                    (
                        KnowledgeVersionState.READY.value,
                        KnowledgeVersionState.DEGRADED.value,
                    )
                ),
                KnowledgeBaseVersionORM.published_at.is_not(None),
            )
        )
        if page_no is not None:
            base_stmt = base_stmt.where(
                KnowledgeChunkModel.page_no == page_no
            )
        if cursor_key is not None:
            key_page, key_ordinal, key_id = cursor_key
            anchor_page_predicate = (
                KnowledgeChunkModel.page_no.is_(None)
                if key_page is None
                else KnowledgeChunkModel.page_no == key_page
            )
            anchor_stmt = (
                base_stmt
                .with_only_columns(KnowledgeChunkModel.id)
                .where(
                    KnowledgeChunkModel.id == key_id,
                    KnowledgeChunkModel.ordinal == key_ordinal,
                    anchor_page_predicate,
                )
                .order_by(None)
            )
            anchor_result = await self.db_session.execute(anchor_stmt)
            if anchor_result.scalar_one_or_none() is None:
                raise ValueError(
                    "document cursor anchor does not exist in requested source"
                )
        count_stmt = select(func.count()).select_from(
            base_stmt.with_only_columns(
                KnowledgeChunkModel.id
            ).order_by(None).subquery()
        )
        total_result = await self.db_session.execute(count_stmt)
        total = int(total_result.scalar_one())

        page_stmt = base_stmt
        if cursor_key is not None:
            key_page, key_ordinal, key_id = cursor_key
            same_page_after = or_(
                KnowledgeChunkModel.ordinal > key_ordinal,
                and_(
                    KnowledgeChunkModel.ordinal == key_ordinal,
                    KnowledgeChunkModel.id > key_id,
                ),
            )
            if key_page is None:
                page_stmt = page_stmt.where(
                    or_(
                        KnowledgeChunkModel.page_no.is_not(None),
                        and_(
                            KnowledgeChunkModel.page_no.is_(None),
                            same_page_after,
                        ),
                    )
                )
            else:
                page_stmt = page_stmt.where(
                    or_(
                        KnowledgeChunkModel.page_no > key_page,
                        and_(
                            KnowledgeChunkModel.page_no == key_page,
                            same_page_after,
                        ),
                    )
                )
        page_stmt = (
            page_stmt
            .order_by(
                KnowledgeChunkModel.page_no.asc().nulls_first(),
                KnowledgeChunkModel.ordinal.asc(),
                KnowledgeChunkModel.id.asc(),
            )
            .limit(limit + 1)
        )
        page_result = await self.db_session.execute(page_stmt)
        chunks = [
            record.to_domain()
            for record in page_result.scalars().all()
        ]
        has_more = len(chunks) > limit
        page_chunks = chunks[:limit]
        next_cursor = (
            _encode_document_cursor(
                kb_id=kb_id,
                version_id=version_id,
                doc_id=doc_id,
                document_revision_id=document_revision_id,
                page_no=page_no,
                chunk=page_chunks[-1],
            )
            if has_more and page_chunks
            else None
        )
        return DocumentPage(
            items=tuple(
                DocumentPageItem.from_chunk(chunk)
                for chunk in page_chunks
            ),
            next_cursor=next_cursor,
            total=total,
            truncated=next_cursor is not None,
        )

    async def get_parents_by_ids(self, parent_ids: List[str]) -> List[KnowledgeChunk]:
        if not parent_ids:
            return []
        stmt = (
            select(KnowledgeChunkModel)
            .join(
                KnowledgeBaseModel,
                KnowledgeBaseModel.id == KnowledgeChunkModel.kb_id,
            )
            .join(
                KnowledgeBaseVersionORM,
                self._active_version_join_predicate(),
            )
            .where(KnowledgeChunkModel.id.in_(parent_ids))
            .where(
                self._active_version_row_predicate(
                    KnowledgeChunkModel.version_id
                )
            )
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def get_chunks_by_ids(self, chunk_ids: List[str]) -> List[KnowledgeChunk]:
        if not chunk_ids:
            return []
        stmt = (
            select(KnowledgeChunkModel)
            .join(
                KnowledgeBaseModel,
                KnowledgeBaseModel.id == KnowledgeChunkModel.kb_id,
            )
            .join(
                KnowledgeBaseVersionORM,
                self._active_version_join_predicate(),
            )
            .where(KnowledgeChunkModel.id.in_(chunk_ids))
            .where(
                self._active_version_row_predicate(
                    KnowledgeChunkModel.version_id
                )
            )
        )
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
            .join(
                KnowledgeBaseModel,
                KnowledgeBaseModel.id == KnowledgeChunkModel.kb_id,
            )
            .join(
                KnowledgeBaseVersionORM,
                self._active_version_join_predicate(),
            )
            .where(KnowledgeChunkModel.doc_id == doc_id)
            .where(
                self._active_version_row_predicate(
                    KnowledgeChunkModel.version_id
                )
            )
            .order_by(KnowledgeChunkModel.ordinal.asc())
            .limit(max(1, min(limit, 200)))
        )
        if page_no is not None:
            stmt = stmt.where(KnowledgeChunkModel.page_no == page_no)
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    @staticmethod
    def _row_to_chunk_doc_score(row) -> Tuple[KnowledgeChunk, KnowledgeDocument, float]:
        chunk = KnowledgeChunk(
            id=row.id,
            kb_id=row.kb_id,
            doc_id=row.doc_id,
            version_id=row.version_id,
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

    @classmethod
    def _row_to_versioned_chunk(cls, row) -> VersionedKnowledgeChunk:
        chunk, document, score = cls._row_to_chunk_doc_score(row)
        revision_id = str(row.document_revision_id or "").strip()
        if not revision_id:
            raise ValueError(
                "versioned knowledge row is missing manifest revision"
            )
        return VersionedKnowledgeChunk(
            chunk=chunk,
            document=document,
            document_revision_id=revision_id,
            score=score,
        )

    @staticmethod
    def _valid_embedding(value: object) -> bool:
        if (
            not isinstance(value, (list, tuple))
            or len(value) != KNOWLEDGE_EMBEDDING_DIMENSION
        ):
            return False
        try:
            return all(math.isfinite(float(item)) for item in value)
        except (TypeError, ValueError):
            return False
