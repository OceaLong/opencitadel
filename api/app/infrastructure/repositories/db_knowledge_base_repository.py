#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.models.knowledge_base import (
    DocStatus,
    KBStatus,
    KnowledgeBase,
    KnowledgeDocument,
)
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from app.domain.repositories.patch import UNSET, UnsetType
from app.infrastructure.models.knowledge_base import (
    KnowledgeBaseModel,
    KnowledgeDocumentModel,
)
from app.infrastructure.models.knowledge_version import (
    KnowledgeBaseVersionORM,
    KnowledgeVersionDocumentORM,
)
from app.infrastructure.repositories.kb._shared import (
    build_versioned_vector_search_statement,  # noqa: F401 re-export for external/test imports
)
from app.infrastructure.repositories.kb.graph_mixin import KBGraphMixin
from app.infrastructure.repositories.kb.index_mixin import KBIndexMixin
from app.infrastructure.repositories.kb.retrieval_mixin import KBRetrievalMixin


class DBKnowledgeBaseRepository(
    KBIndexMixin, KBGraphMixin, KBRetrievalMixin, KnowledgeBaseRepository
):
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
