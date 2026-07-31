#!/usr/bin/env python
# -*- coding: utf-8 -*-
import base64
import binascii
from datetime import datetime
import json
import uuid
import math
import unicodedata
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, case, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.models.knowledge_base import (
    ChunkLevel,
    DocStatus,
    KBStatus,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEntity,
    KnowledgeEntityRef,
    KnowledgeRelation,
)
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.knowledge_base_repository import (
    DocumentPage,
    DocumentPageItem,
    KNOWLEDGE_EMBEDDING_DIMENSION,
    KnowledgeBaseRepository,
    VersionedKnowledgeChunk,
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
from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeVersionState,
)


_CHUNK_INSERT_BATCH_SIZE = 500


def _is_cursor_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _encode_document_cursor(
    *,
    kb_id: str,
    version_id: str,
    doc_id: str,
    document_revision_id: str,
    page_no: Optional[int],
    chunk: KnowledgeChunk,
) -> str:
    payload = {
        "kb": kb_id,
        "version": version_id,
        "document": doc_id,
        "revision": document_revision_id,
        "page": page_no,
        "key": [chunk.page_no, chunk.ordinal, chunk.id],
    }
    encoded = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _decode_document_cursor(
    cursor: str,
    *,
    kb_id: str,
    version_id: str,
    doc_id: str,
    document_revision_id: str,
    page_no: Optional[int],
) -> tuple[Optional[int], int, str]:
    try:
        if not isinstance(cursor, str) or not cursor or len(cursor) > 4096:
            raise ValueError
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
    except (
        ValueError,
        TypeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("invalid document cursor") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid document cursor")
    if (
        payload.get("kb") != kb_id
        or payload.get("version") != version_id
        or payload.get("document") != doc_id
        or payload.get("revision") != document_revision_id
        or payload.get("page") != page_no
    ):
        raise ValueError("document cursor does not match requested source")
    key = payload.get("key")
    if not isinstance(key, list) or len(key) != 3:
        raise ValueError("invalid document cursor key")
    key_page, key_ordinal, key_id = key
    if (
        (
            key_page is not None
            and (
                not _is_cursor_int(key_page)
                or key_page < 1
            )
        )
        or not _is_cursor_int(key_ordinal)
        or key_ordinal < 0
        or not isinstance(key_id, str)
        or not key_id
    ):
        raise ValueError("invalid document cursor key")
    if page_no is not None and key_page != page_no:
        raise ValueError(
            "document cursor key does not match requested page filter"
        )
    return key_page, key_ordinal, key_id

_CHUNK_INSERT_WITH_EMBEDDING_SQL = text(
    """
    INSERT INTO knowledge_chunks
        (id, kb_id, doc_id, version_id, parent_id, level, content, content_tsv,
         page_no, heading_path, ordinal, embedding)
    VALUES
        (:id, :kb_id, :doc_id, :version_id, :parent_id, :level, :content,
         CASE
           WHEN :content_tsv IS NULL
             THEN to_tsvector('simple', :segmented_content)
           ELSE CAST(:content_tsv AS tsvector)
         END,
         :page_no, :heading_path, :ordinal, :embedding::vector)
    """
)

_CHUNK_INSERT_PLAIN_SQL = text(
    """
    INSERT INTO knowledge_chunks
        (id, kb_id, doc_id, version_id, parent_id, level, content, content_tsv,
         page_no, heading_path, ordinal)
    VALUES
        (:id, :kb_id, :doc_id, :version_id, :parent_id, :level, :content,
         CASE
           WHEN :content_tsv IS NULL
             THEN to_tsvector('simple', :segmented_content)
           ELSE CAST(:content_tsv AS tsvector)
         END,
         :page_no, :heading_path, :ordinal)
    """
)

_VERSIONED_VECTOR_SEARCH_SQL = """
SELECT c.id, c.kb_id, c.doc_id, c.version_id,
       c.parent_id, c.level, c.content,
       c.page_no, c.heading_path, c.ordinal,
       d.title, d.source_type, d.source_ref, d.mime, d.file_id,
       d.page_count, d.status, d.error, d.warning,
       d.created_at, d.updated_at,
       manifest.document_revision_id,
       1 - (c.embedding <=> :query::vector) AS score
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
  AND c.embedding IS NOT NULL
ORDER BY c.embedding <=> :query::vector,
         c.ordinal ASC,
         c.id ASC
LIMIT :limit
""".strip()


def build_versioned_vector_search_statement(
    *,
    explain: bool = False,
):
    """Single production-owned SQL shape used by retrieval and ANN gate."""
    prefix = (
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)\n"
        if explain
        else ""
    )
    return text(prefix + _VERSIONED_VECTOR_SEARCH_SQL)


class DBKnowledgeBaseRepository(KnowledgeBaseRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    def _apply_scope(self, stmt, scope: Optional[OwnerScope]):
        if scope is None:
            return stmt
        if scope.type == OwnerScopeType.TEAM:
            return stmt.where(KnowledgeBaseModel.team_id == scope.team_id)
        return stmt.where(KnowledgeBaseModel.owner_user_id == scope.user_id, KnowledgeBaseModel.team_id.is_(None))

    @staticmethod
    def _active_version_join_predicate():
        """Close every public read over one authoritative active version."""
        return and_(
            KnowledgeBaseVersionORM.id
            == KnowledgeBaseModel.active_version_id,
            KnowledgeBaseVersionORM.knowledge_base_id
            == KnowledgeBaseModel.id,
        )

    @staticmethod
    def _active_version_row_predicate(version_column):
        """Expose legacy NULL rows only during the c7 legacy snapshot window."""
        return or_(
            version_column == KnowledgeBaseModel.active_version_id,
            and_(
                version_column.is_(None),
                KnowledgeBaseVersionORM.legacy_snapshot.is_(True),
            ),
        )

    @staticmethod
    def _active_document_predicate():
        """Use the active manifest, with c7 old-writer fallback only."""
        any_manifest = aliased(KnowledgeVersionDocumentORM)
        has_any_manifest = (
            select(any_manifest.version_id)
            .where(
                any_manifest.knowledge_base_id
                == KnowledgeDocumentModel.kb_id,
                any_manifest.document_id == KnowledgeDocumentModel.id,
            )
            .exists()
        )
        return or_(
            KnowledgeVersionDocumentORM.document_id.is_not(None),
            and_(
                KnowledgeBaseVersionORM.legacy_snapshot.is_(True),
                ~has_any_manifest,
            ),
        )

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

    async def get_kb_for_update(
            self,
            kb_id: str,
            scope: Optional[OwnerScope] = None,
    ) -> Optional[KnowledgeBase]:
        stmt = self._apply_scope(
            select(KnowledgeBaseModel)
            .where(KnowledgeBaseModel.id == kb_id)
            .with_for_update(),
            scope,
        )
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

    async def insert_document(self, document: KnowledgeDocument) -> None:
        """Insert-only seam used by immutable candidate construction."""
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
        await self.db_session.flush()

    async def list_documents(self, kb_id: str) -> List[KnowledgeDocument]:
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
                (
                    KnowledgeVersionDocumentORM.document_id
                    == KnowledgeDocumentModel.id
                )
                & (
                    KnowledgeVersionDocumentORM.knowledge_base_id
                    == KnowledgeDocumentModel.kb_id
                )
                & (
                    KnowledgeBaseModel.active_version_id
                    == KnowledgeVersionDocumentORM.version_id
                ),
            )
            .where(KnowledgeDocumentModel.kb_id == kb_id)
            .where(self._active_document_predicate())
            .order_by(KnowledgeDocumentModel.created_at.asc())
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def get_document(self, doc_id: str) -> Optional[KnowledgeDocument]:
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
                (
                    KnowledgeVersionDocumentORM.document_id
                    == KnowledgeDocumentModel.id
                )
                & (
                    KnowledgeVersionDocumentORM.knowledge_base_id
                    == KnowledgeDocumentModel.kb_id
                )
                & (
                    KnowledgeVersionDocumentORM.version_id
                    == KnowledgeBaseModel.active_version_id
                ),
            )
            .where(KnowledgeDocumentModel.id == doc_id)
            .where(self._active_document_predicate())
        )
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def get_document_for_build(
            self,
            doc_id: str,
    ) -> Optional[KnowledgeDocument]:
        result = await self.db_session.execute(
            select(KnowledgeDocumentModel).where(
                KnowledgeDocumentModel.id == doc_id
            )
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def update_document_status(
            self,
            doc_id: str,
            status: DocStatus,
            error: str | None | UnsetType = UNSET,
            warning: str | None | UnsetType = UNSET,
            page_count: Optional[int] = None,
    ) -> None:
        values = {"status": status.value, "updated_at": datetime.now()}
        if error is not UNSET:
            values["error"] = error
        if warning is not UNSET:
            values["warning"] = warning
        if page_count is not None:
            values["page_count"] = page_count
        await self.db_session.execute(
            update(KnowledgeDocumentModel).where(KnowledgeDocumentModel.id == doc_id).values(**values)
        )

    async def delete_document(self, doc_id: str) -> None:
        await self.purge_documents_index_data([doc_id])
        await self.db_session.execute(
            delete(KnowledgeDocumentModel).where(KnowledgeDocumentModel.id == doc_id)
        )

    async def count_documents(self, kb_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(KnowledgeDocumentModel)
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
                (
                    KnowledgeVersionDocumentORM.document_id
                    == KnowledgeDocumentModel.id
                )
                & (
                    KnowledgeVersionDocumentORM.knowledge_base_id
                    == KnowledgeDocumentModel.kb_id
                )
                & (
                    KnowledgeVersionDocumentORM.version_id
                    == KnowledgeBaseModel.active_version_id
                ),
            )
            .where(KnowledgeDocumentModel.kb_id == kb_id)
            .where(self._active_document_predicate())
        )
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one())

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
        embedded = [c for c in chunks if c.embedding]
        plain = [c for c in chunks if not c.embedding]
        for batch_source, sql in ((embedded, _CHUNK_INSERT_WITH_EMBEDDING_SQL), (plain, _CHUNK_INSERT_PLAIN_SQL)):
            for start in range(0, len(batch_source), _CHUNK_INSERT_BATCH_SIZE):
                batch = batch_source[start:start + _CHUNK_INSERT_BATCH_SIZE]
                await self.db_session.execute(sql, [self._chunk_params(chunk) for chunk in batch])

    async def replace_candidate_chunks(
            self,
            kb_id: str,
            version_id: str,
            chunks: List[KnowledgeChunk],
    ) -> None:
        await self._require_building_candidate(kb_id, version_id)
        if any(
            chunk.kb_id != kb_id or chunk.version_id != version_id
            for chunk in chunks
        ):
            raise ValueError("candidate chunks must carry exact version ownership")
        await self._delete_candidate_graph(version_id)
        await self.db_session.execute(
            delete(KnowledgeChunkModel).where(
                KnowledgeChunkModel.kb_id == kb_id,
                KnowledgeChunkModel.version_id == version_id,
            )
        )
        await self.save_chunks(chunks)

    async def clone_version_chunks(
            self,
            kb_id: str,
            source_version_id: str,
            target_version_id: str,
            document_ids: List[str],
    ) -> List[KnowledgeChunk]:
        """Read active source rows and deterministically remap them to target."""
        if not document_ids:
            return []
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("legacy clone document ids must be unique")
        await self._require_building_candidate(kb_id, target_version_id)
        ownership = await self.db_session.execute(
            text(
                """
                SELECT count(*)
                FROM knowledge_bases kb
                JOIN knowledge_base_versions target
                  ON target.id = :target_version_id
                 AND target.knowledge_base_id = kb.id
                JOIN knowledge_base_versions source
                  ON source.id = :source_version_id
                 AND source.knowledge_base_id = kb.id
                WHERE kb.id = :kb_id
                  AND kb.active_version_id = source.id
                  AND target.parent_version_id = source.id
                  AND target.state = 'building'
                  AND target.published_at IS NULL
                  AND source.published_at IS NOT NULL
                  AND source.state IN ('ready', 'degraded')
                """
            ),
            {
                "kb_id": kb_id,
                "source_version_id": source_version_id,
                "target_version_id": target_version_id,
            },
        )
        if int(ownership.scalar_one()) != 1:
            raise ValueError(
                "legacy clone requires the exact active published parent"
            )
        manifest_result = await self.db_session.execute(
            text(
                """
                SELECT count(*)
                FROM knowledge_base_version_documents target_manifest
                JOIN knowledge_base_version_documents source_manifest
                  ON source_manifest.version_id = :source_version_id
                 AND source_manifest.knowledge_base_id =
                     target_manifest.knowledge_base_id
                 AND source_manifest.document_id =
                     target_manifest.document_id
                 AND source_manifest.document_revision_id =
                     target_manifest.document_revision_id
                 AND source_manifest.state = 'indexed'
                JOIN knowledge_document_revisions revision
                  ON revision.id = target_manifest.document_revision_id
                 AND revision.document_id = target_manifest.document_id
                 AND revision.state = 'indexed'
                 AND revision.needs_chunk_clone IS TRUE
                 AND revision.parsed_blocks = '[]'::jsonb
                WHERE target_manifest.version_id = :target_version_id
                  AND target_manifest.knowledge_base_id = :kb_id
                  AND target_manifest.state = 'indexed'
                  AND target_manifest.document_id = ANY(:document_ids)
                """
            ),
            {
                "kb_id": kb_id,
                "source_version_id": source_version_id,
                "target_version_id": target_version_id,
                "document_ids": document_ids,
            },
        )
        if int(manifest_result.scalar_one()) != len(document_ids):
            raise ValueError(
                "legacy clone requires exact marked revision identity "
                "in source and target manifests"
            )
        result = await self.db_session.execute(
            text(
                """
                SELECT id, kb_id, doc_id, parent_id, level, content,
                       page_no, heading_path, ordinal,
                       embedding::text AS embedding_text,
                       content_tsv::text AS content_tsv_text
                FROM knowledge_chunks
                WHERE kb_id = :kb_id
                  AND version_id = :source_version_id
                  AND doc_id = ANY(:document_ids)
                ORDER BY doc_id,
                         CASE level WHEN 'parent' THEN 0 ELSE 1 END,
                         ordinal,
                         id
                """
            ),
            {
                "kb_id": kb_id,
                "source_version_id": source_version_id,
                "document_ids": document_ids,
            },
        )
        rows = list(result.fetchall())
        returned_docs = {str(row.doc_id) for row in rows}
        if returned_docs != set(document_ids):
            raise ValueError(
                "legacy active chunks are incomplete for retained manifest"
            )
        id_map = {
            str(row.id): str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"opencitadel:kb-chunk:{target_version_id}:{row.id}",
                )
            )
            for row in rows
        }
        clones: list[KnowledgeChunk] = []
        for row in rows:
            parent_id = (
                id_map.get(str(row.parent_id))
                if row.parent_id is not None
                else None
            )
            if row.parent_id is not None and parent_id is None:
                raise ValueError(
                    "legacy child-parent clone closure is incomplete"
                )
            clones.append(
                KnowledgeChunk(
                    id=id_map[str(row.id)],
                    kb_id=kb_id,
                    doc_id=str(row.doc_id),
                    version_id=target_version_id,
                    parent_id=parent_id,
                    level=ChunkLevel(str(row.level)),
                    content=str(row.content or ""),
                    segmented_content=str(row.content or ""),
                    content_tsv=(
                        str(row.content_tsv_text)
                        if row.content_tsv_text is not None
                        else None
                    ),
                    embedding=_parse_vector_text(
                        getattr(row, "embedding_text", None)
                    ),
                    page_no=row.page_no,
                    heading_path=str(row.heading_path or ""),
                    ordinal=int(row.ordinal or 0),
                )
            )
        return clones

    async def replace_candidate_graph(
            self,
            kb_id: str,
            version_id: str,
            entities: List[KnowledgeEntity],
            relations: List[KnowledgeRelation],
            refs: List[KnowledgeEntityRef],
    ) -> None:
        await self._require_building_candidate(kb_id, version_id)
        rows = [*entities, *relations, *refs]
        if any(
            row.kb_id != kb_id or row.version_id != version_id
            for row in rows
        ):
            raise ValueError("candidate graph rows must carry exact version ownership")
        await self._delete_candidate_graph(version_id)
        self.db_session.add_all(
            [
                KnowledgeEntityModel(
                    id=entity.id,
                    kb_id=entity.kb_id,
                    version_id=entity.version_id,
                    name=entity.name,
                    normalized_name=(
                        entity.normalized_name
                        or entity.name.strip().lower()
                    ),
                    type=entity.type,
                    description=entity.description,
                )
                for entity in entities
            ]
        )
        await self.db_session.flush()
        self.db_session.add_all(
            [
                KnowledgeRelationModel(
                    id=relation.id,
                    kb_id=relation.kb_id,
                    version_id=relation.version_id,
                    src_entity_id=relation.src_entity_id,
                    dst_entity_id=relation.dst_entity_id,
                    relation=relation.relation,
                    chunk_id=relation.chunk_id,
                )
                for relation in relations
            ]
        )
        self.db_session.add_all(
            [
                KnowledgeEntityRefModel(
                    id=ref.id,
                    kb_id=ref.kb_id,
                    version_id=ref.version_id,
                    entity_id=ref.entity_id,
                    doc_id=ref.doc_id,
                )
                for ref in refs
            ]
        )
        await self.db_session.flush()

    async def upsert_candidate_graph_batch(
            self,
            kb_id: str,
            version_id: str,
            entities: List[KnowledgeEntity],
            relations: List[KnowledgeRelation],
            refs: List[KnowledgeEntityRef],
    ) -> None:
        """Merge one extraction batch without a select-then-insert race."""
        if not kb_id.strip() or not version_id.strip():
            raise ValueError("candidate graph identity cannot be empty")
        await self._require_building_candidate(kb_id, version_id)
        rows = [*entities, *relations, *refs]
        if any(
            row.kb_id != kb_id or row.version_id != version_id
            for row in rows
        ):
            raise ValueError(
                "candidate graph rows must carry exact version ownership"
            )

        temp_to_persistent: dict[str, str] = {}
        for entity in sorted(
            entities,
            key=lambda item: (
                item.normalized_name,
                item.type,
                item.id,
            ),
        ):
            normalized_name = _normalize_graph_identity(entity.name)
            entity_type = _normalize_graph_identity(entity.type)
            if not normalized_name or not entity_type:
                raise ValueError(
                    "candidate entity identity requires normalized name/type"
                )
            if entity.normalized_name != normalized_name:
                raise ValueError(
                    "candidate entity normalized_name is not canonical"
                )
            insert_stmt = pg_insert(KnowledgeEntityModel).values(
                id=entity.id,
                kb_id=kb_id,
                version_id=version_id,
                name=entity.name,
                normalized_name=normalized_name,
                type=entity_type,
                description=entity.description,
            )
            statement = insert_stmt.on_conflict_do_update(
                index_elements=[
                    KnowledgeEntityModel.version_id,
                    KnowledgeEntityModel.normalized_name,
                    KnowledgeEntityModel.type,
                ],
                set_={
                    "name": func.least(
                        KnowledgeEntityModel.name,
                        insert_stmt.excluded.name,
                    ),
                    "description": case(
                        (
                            func.length(insert_stmt.excluded.description)
                            > func.length(
                                KnowledgeEntityModel.description
                            ),
                            insert_stmt.excluded.description,
                        ),
                        (
                            func.length(insert_stmt.excluded.description)
                            < func.length(
                                KnowledgeEntityModel.description
                            ),
                            KnowledgeEntityModel.description,
                        ),
                        else_=func.least(
                            KnowledgeEntityModel.description,
                            insert_stmt.excluded.description,
                        ),
                    ),
                },
            ).returning(KnowledgeEntityModel.id)
            result = await self.db_session.execute(statement)
            persistent_id = result.scalar_one()
            temp_to_persistent[entity.id] = str(persistent_id)

        remapped_relations: list[dict[str, object]] = []
        for relation in relations:
            src_id = temp_to_persistent.get(relation.src_entity_id)
            dst_id = temp_to_persistent.get(relation.dst_entity_id)
            if src_id is None or dst_id is None:
                raise ValueError(
                    "candidate relation endpoints are not in its entity batch"
                )
            remapped_relations.append(
                {
                    "id": relation.id,
                    "kb_id": kb_id,
                    "version_id": version_id,
                    "src_entity_id": src_id,
                    "dst_entity_id": dst_id,
                    "relation": relation.relation,
                    "chunk_id": relation.chunk_id,
                }
            )
        if remapped_relations:
            await self.db_session.execute(
                pg_insert(KnowledgeRelationModel)
                .values(remapped_relations)
                .on_conflict_do_nothing(
                    index_elements=[KnowledgeRelationModel.id]
                )
            )

        remapped_refs: list[dict[str, object]] = []
        for ref in refs:
            entity_id = temp_to_persistent.get(ref.entity_id)
            if entity_id is None:
                raise ValueError(
                    "candidate entity reference is not in its entity batch"
                )
            remapped_refs.append(
                {
                    "id": ref.id,
                    "kb_id": kb_id,
                    "version_id": version_id,
                    "entity_id": entity_id,
                    "doc_id": ref.doc_id,
                }
            )
        if remapped_refs:
            await self.db_session.execute(
                pg_insert(KnowledgeEntityRefModel)
                .values(remapped_refs)
                .on_conflict_do_nothing(
                    index_elements=[
                        KnowledgeEntityRefModel.entity_id,
                        KnowledgeEntityRefModel.doc_id,
                    ]
                )
            )
        await self.db_session.flush()

    async def get_candidate_index_metrics(
            self,
            kb_id: str,
            version_id: str,
    ) -> Dict[str, int]:
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
                        (
                            KnowledgeVersionDocumentORM.version_id
                            == KnowledgeChunkModel.version_id
                        )
                        & (
                            KnowledgeVersionDocumentORM.document_id
                            == KnowledgeChunkModel.doc_id
                        ),
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
                KnowledgeBaseVersionORM.state
                == KnowledgeVersionState.BUILDING.value,
                KnowledgeBaseVersionORM.published_at.is_(None),
            )
        )
        if result.scalar_one_or_none() is None:
            raise ValueError("candidate-scoped write requires a building version")

    async def _delete_candidate_graph(self, version_id: str) -> None:
        await self.db_session.execute(
            delete(KnowledgeRelationModel).where(
                KnowledgeRelationModel.version_id == version_id
            )
        )
        await self.db_session.execute(
            delete(KnowledgeEntityRefModel).where(
                KnowledgeEntityRefModel.version_id == version_id
            )
        )
        await self.db_session.execute(
            delete(KnowledgeEntityModel).where(
                KnowledgeEntityModel.version_id == version_id
            )
        )

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

    async def upsert_entities(self, entities: List[KnowledgeEntity]) -> Dict[str, str]:
        keyed: Dict[str, KnowledgeEntity] = {}
        for entity in entities:
            key = entity.name.strip().lower()
            if key:
                keyed.setdefault(key, entity)
        if not keyed:
            return {}
        kb_id = next(iter(keyed.values())).kb_id
        stmt = (
            select(KnowledgeEntityModel)
            .where(KnowledgeEntityModel.kb_id == kb_id)
            .where(func.lower(KnowledgeEntityModel.name).in_(list(keyed)))
        )
        result = await self.db_session.execute(stmt)
        existing = {record.name.strip().lower(): record.id for record in result.scalars().all()}
        id_map: Dict[str, str] = {}
        for key, entity in keyed.items():
            if key in existing:
                id_map[key] = existing[key]
                continue
            self.db_session.add(
                KnowledgeEntityModel(
                    id=entity.id,
                    kb_id=entity.kb_id,
                    name=entity.name,
                    type=entity.type,
                    description=entity.description,
                )
            )
            id_map[key] = entity.id
        return id_map

    async def save_entity_refs(self, refs: List[KnowledgeEntityRef]) -> None:
        sql = text(
            """
            INSERT INTO knowledge_entity_refs (id, kb_id, entity_id, doc_id)
            VALUES (:id, :kb_id, :entity_id, :doc_id)
            ON CONFLICT (entity_id, doc_id) DO NOTHING
            """
        )
        for start in range(0, len(refs), _CHUNK_INSERT_BATCH_SIZE):
            batch = refs[start:start + _CHUNK_INSERT_BATCH_SIZE]
            await self.db_session.execute(
                sql,
                [
                    {"id": ref.id, "kb_id": ref.kb_id, "entity_id": ref.entity_id, "doc_id": ref.doc_id}
                    for ref in batch
                ],
            )

    async def purge_documents_index_data(self, doc_ids: List[str]) -> None:
        if not doc_ids:
            return
        chunk_ids_stmt = select(KnowledgeChunkModel.id).where(KnowledgeChunkModel.doc_id.in_(doc_ids))
        await self.db_session.execute(
            delete(KnowledgeRelationModel).where(KnowledgeRelationModel.chunk_id.in_(chunk_ids_stmt))
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

    async def count_ready_documents(self, kb_ids: List[str]) -> Dict[str, int]:
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
                (
                    KnowledgeVersionDocumentORM.document_id
                    == KnowledgeDocumentModel.id
                )
                & (
                    KnowledgeVersionDocumentORM.knowledge_base_id
                    == KnowledgeDocumentModel.kb_id
                )
                & (
                    KnowledgeVersionDocumentORM.version_id
                    == KnowledgeBaseModel.active_version_id
                ),
            )
            .where(KnowledgeDocumentModel.kb_id.in_(kb_ids))
            .where(self._active_document_predicate())
            .where(
                or_(
                    KnowledgeVersionDocumentORM.state == "indexed",
                    and_(
                        KnowledgeVersionDocumentORM.document_id.is_(None),
                        KnowledgeBaseVersionORM.legacy_snapshot.is_(True),
                        KnowledgeDocumentModel.status
                        == DocStatus.READY.value,
                    ),
                )
            )
            .group_by(KnowledgeDocumentModel.kb_id)
        )
        result = await self.db_session.execute(stmt)
        counts = {kb_id: 0 for kb_id in kb_ids}
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
            .where(
                self._active_version_row_predicate(
                    KnowledgeChunkModel.version_id
                )
            )
            .where(KnowledgeChunkModel.level == ChunkLevel.CHILD.value)
        )
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one())

    async def list_documents_page(
            self,
            kb_id: str,
            limit: int = 50,
            offset: int = 0,
    ) -> Tuple[List[KnowledgeDocument], int]:
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
                (
                    KnowledgeVersionDocumentORM.document_id
                    == KnowledgeDocumentModel.id
                )
                & (
                    KnowledgeVersionDocumentORM.knowledge_base_id
                    == KnowledgeDocumentModel.kb_id
                )
                & (
                    KnowledgeVersionDocumentORM.version_id
                    == KnowledgeBaseModel.active_version_id
                ),
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
            .values(status=DocStatus.PENDING.value, error=None, updated_at=datetime.now())
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
        stmt = (
            select(KnowledgeEntityModel)
            .join(
                KnowledgeBaseModel,
                KnowledgeBaseModel.id == KnowledgeEntityModel.kb_id,
            )
            .join(
                KnowledgeBaseVersionORM,
                self._active_version_join_predicate(),
            )
            .where(KnowledgeEntityModel.kb_id == kb_id)
            .where(
                self._active_version_row_predicate(
                    KnowledgeEntityModel.version_id
                )
            )
        )
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
            .join(
                KnowledgeBaseModel,
                KnowledgeBaseModel.id == KnowledgeRelationModel.kb_id,
            )
            .join(
                KnowledgeBaseVersionORM,
                self._active_version_join_predicate(),
            )
            .where(KnowledgeRelationModel.kb_id == kb_id)
            .where(
                self._active_version_row_predicate(
                    KnowledgeRelationModel.version_id
                )
            )
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
            .join(
                KnowledgeBaseModel,
                KnowledgeBaseModel.id == KnowledgeRelationModel.kb_id,
            )
            .join(
                KnowledgeBaseVersionORM,
                self._active_version_join_predicate(),
            )
            .where(KnowledgeRelationModel.kb_id == kb_id)
            .where(
                self._active_version_row_predicate(
                    KnowledgeRelationModel.version_id
                )
            )
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
            .join(
                KnowledgeBaseModel,
                KnowledgeBaseModel.id == KnowledgeRelationModel.kb_id,
            )
            .join(
                KnowledgeBaseVersionORM,
                self._active_version_join_predicate(),
            )
            .where(KnowledgeRelationModel.kb_id == kb_id)
            .where(
                self._active_version_row_predicate(
                    KnowledgeRelationModel.version_id
                )
            )
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

    async def list_entities_for_version(
            self,
            kb_id: str,
            version_id: str,
            name: Optional[str] = None,
    ) -> List[KnowledgeEntity]:
        stmt = (
            select(KnowledgeEntityModel)
            .join(
                KnowledgeBaseVersionORM,
                (
                    KnowledgeBaseVersionORM.id
                    == KnowledgeEntityModel.version_id
                )
                & (
                    KnowledgeBaseVersionORM.knowledge_base_id
                    == KnowledgeEntityModel.kb_id
                ),
            )
            .where(
                KnowledgeEntityModel.kb_id == kb_id,
                KnowledgeEntityModel.version_id == version_id,
                KnowledgeBaseVersionORM.state.in_(
                    (
                        KnowledgeVersionState.READY.value,
                        KnowledgeVersionState.DEGRADED.value,
                    )
                ),
                KnowledgeBaseVersionORM.published_at.is_not(None),
            )
        )
        if name:
            stmt = stmt.where(
                KnowledgeEntityModel.name.ilike(f"%{name}%")
            )
        stmt = stmt.order_by(
            KnowledgeEntityModel.normalized_name.asc(),
            KnowledgeEntityModel.id.asc(),
        ).limit(100)
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def list_entities_page_for_version(
            self,
            kb_id: str,
            version_id: str,
            *,
            q: Optional[str],
            after: Optional[Tuple[str, str]],
            limit: int,
    ) -> Tuple[List[KnowledgeEntity], Optional[Tuple[str, str]]]:
        bounded_limit = max(1, min(limit, 100))
        stmt = (
            select(KnowledgeEntityModel)
            .join(
                KnowledgeBaseVersionORM,
                (
                    KnowledgeBaseVersionORM.id
                    == KnowledgeEntityModel.version_id
                )
                & (
                    KnowledgeBaseVersionORM.knowledge_base_id
                    == KnowledgeEntityModel.kb_id
                ),
            )
            .where(
                KnowledgeEntityModel.kb_id == kb_id,
                KnowledgeEntityModel.version_id == version_id,
                KnowledgeBaseVersionORM.state.in_(
                    (
                        KnowledgeVersionState.READY.value,
                        KnowledgeVersionState.DEGRADED.value,
                    )
                ),
                KnowledgeBaseVersionORM.published_at.is_not(None),
                KnowledgeEntityModel.normalized_name.is_not(None),
            )
        )
        if q:
            stmt = stmt.where(
                KnowledgeEntityModel.name.ilike(f"%{q}%")
            )
        if after is not None:
            stmt = stmt.where(
                or_(
                    KnowledgeEntityModel.normalized_name > after[0],
                    and_(
                        KnowledgeEntityModel.normalized_name == after[0],
                        KnowledgeEntityModel.id > after[1],
                    ),
                )
            )
        result = await self.db_session.execute(
            stmt.order_by(
                KnowledgeEntityModel.normalized_name.asc(),
                KnowledgeEntityModel.id.asc(),
            ).limit(bounded_limit + 1)
        )
        records = list(result.scalars().all())
        has_more = len(records) > bounded_limit
        records = records[:bounded_limit]
        next_key = None
        if has_more and records:
            last = records[-1]
            next_key = (str(last.normalized_name), str(last.id))
        return [record.to_domain() for record in records], next_key

    async def get_entities_by_ids_for_version(
            self,
            kb_id: str,
            version_id: str,
            entity_ids: List[str],
    ) -> List[KnowledgeEntity]:
        if not entity_ids:
            return []
        stmt = (
            select(KnowledgeEntityModel)
            .join(
                KnowledgeBaseVersionORM,
                (
                    KnowledgeBaseVersionORM.id
                    == KnowledgeEntityModel.version_id
                )
                & (
                    KnowledgeBaseVersionORM.knowledge_base_id
                    == KnowledgeEntityModel.kb_id
                ),
            )
            .where(
                KnowledgeEntityModel.kb_id == kb_id,
                KnowledgeEntityModel.version_id == version_id,
                KnowledgeEntityModel.id.in_(
                    list(dict.fromkeys(entity_ids))
                ),
                KnowledgeBaseVersionORM.state.in_(
                    (
                        KnowledgeVersionState.READY.value,
                        KnowledgeVersionState.DEGRADED.value,
                    )
                ),
                KnowledgeBaseVersionORM.published_at.is_not(None),
            )
            .order_by(
                KnowledgeEntityModel.normalized_name.asc(),
                KnowledgeEntityModel.id.asc(),
            )
        )
        result = await self.db_session.execute(stmt)
        return [
            record.to_domain()
            for record in result.scalars().all()
        ]

    async def list_relations_for_entities_for_version(
            self,
            kb_id: str,
            version_id: str,
            entity_ids: List[str],
    ) -> List[KnowledgeRelation]:
        if not entity_ids:
            return []
        stmt = (
            select(KnowledgeRelationModel)
            .join(
                KnowledgeBaseVersionORM,
                (
                    KnowledgeBaseVersionORM.id
                    == KnowledgeRelationModel.version_id
                )
                & (
                    KnowledgeBaseVersionORM.knowledge_base_id
                    == KnowledgeRelationModel.kb_id
                ),
            )
            .where(
                KnowledgeRelationModel.kb_id == kb_id,
                KnowledgeRelationModel.version_id == version_id,
                KnowledgeBaseVersionORM.state.in_(
                    (
                        KnowledgeVersionState.READY.value,
                        KnowledgeVersionState.DEGRADED.value,
                    )
                ),
                KnowledgeBaseVersionORM.published_at.is_not(None),
                or_(
                    KnowledgeRelationModel.src_entity_id.in_(entity_ids),
                    KnowledgeRelationModel.dst_entity_id.in_(entity_ids),
                ),
            )
            .order_by(
                KnowledgeRelationModel.relation.asc(),
                KnowledgeRelationModel.id.asc(),
            )
            .limit(200)
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def get_related_chunk_ids_for_version(
            self,
            kb_id: str,
            version_id: str,
            chunk_ids: List[str],
            limit: int = 20,
    ) -> List[str]:
        if not chunk_ids:
            return []
        published = (
            select(KnowledgeBaseVersionORM.id)
            .where(
                KnowledgeBaseVersionORM.id == version_id,
                KnowledgeBaseVersionORM.knowledge_base_id == kb_id,
                KnowledgeBaseVersionORM.state.in_(
                    (
                        KnowledgeVersionState.READY.value,
                        KnowledgeVersionState.DEGRADED.value,
                    )
                ),
                KnowledgeBaseVersionORM.published_at.is_not(None),
            )
            .exists()
        )
        seed_result = await self.db_session.execute(
            select(KnowledgeRelationModel)
            .where(
                KnowledgeRelationModel.kb_id == kb_id,
                KnowledgeRelationModel.version_id == version_id,
                KnowledgeRelationModel.chunk_id.in_(chunk_ids),
                published,
            )
            .order_by(KnowledgeRelationModel.id.asc())
        )
        entity_ids: set[str] = set()
        for relation in seed_result.scalars().all():
            entity_ids.add(relation.src_entity_id)
            entity_ids.add(relation.dst_entity_id)
        if not entity_ids:
            return []
        related_result = await self.db_session.execute(
            select(KnowledgeRelationModel.chunk_id)
            .where(
                KnowledgeRelationModel.kb_id == kb_id,
                KnowledgeRelationModel.version_id == version_id,
                KnowledgeRelationModel.chunk_id.is_not(None),
                KnowledgeRelationModel.chunk_id.not_in(chunk_ids),
                published,
                or_(
                    KnowledgeRelationModel.src_entity_id.in_(entity_ids),
                    KnowledgeRelationModel.dst_entity_id.in_(entity_ids),
                ),
            )
            .order_by(
                KnowledgeRelationModel.chunk_id.asc(),
                KnowledgeRelationModel.id.asc(),
            )
            .limit(max(1, min(limit, 100)))
        )
        return list(dict.fromkeys(
            str(chunk_id)
            for chunk_id in related_result.scalars().all()
            if chunk_id
        ))

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


def _parse_vector_text(value: object) -> List[float]:
    if value is None:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    if not raw.strip():
        return []
    return [float(item) for item in raw.split(",")]


def _normalize_graph_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    return " ".join(normalized.strip().casefold().split())
