#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import List, Optional, Protocol, Tuple

from app.domain.models.knowledge_base import (
    DocStatus,
    KBStatus,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEntity,
    KnowledgeRelation,
)
from app.domain.models.scope import OwnerScope


class KnowledgeBaseRepository(Protocol):
    async def save_kb(self, kb: KnowledgeBase) -> None:
        ...

    async def get_kb(self, kb_id: str, scope: Optional[OwnerScope] = None) -> Optional[KnowledgeBase]:
        ...

    async def list_kbs(self, limit: int = 100, offset: int = 0, scope: Optional[OwnerScope] = None) -> List[KnowledgeBase]:
        ...

    async def list_stuck_ingesting(self, limit: int = 100) -> List[KnowledgeBase]:
        ...

    async def delete_kb(self, kb_id: str) -> None:
        ...

    async def update_status(
            self,
            kb_id: str,
            status: KBStatus,
            error: Optional[str] = None,
    ) -> None:
        ...

    async def save_document(self, document: KnowledgeDocument) -> None:
        ...

    async def list_documents(self, kb_id: str) -> List[KnowledgeDocument]:
        ...

    async def get_document(self, doc_id: str) -> Optional[KnowledgeDocument]:
        ...

    async def update_document_status(
            self,
            doc_id: str,
            status: DocStatus,
            error: Optional[str] = None,
            warning: Optional[str] = None,
            page_count: Optional[int] = None,
    ) -> None:
        ...

    async def clear_index_data(self, kb_id: str) -> None:
        ...

    async def replace_index_chunks(self, kb_id: str, chunks: List[KnowledgeChunk]) -> None:
        """Atomically replace all chunk index data for a knowledge base."""
        ...

    async def save_chunks(self, chunks: List[KnowledgeChunk]) -> None:
        ...

    async def vector_search_chunks(
            self,
            kb_id: str,
            query_embedding: List[float],
            limit: int = 20,
    ) -> List[Tuple[KnowledgeChunk, KnowledgeDocument, float]]:
        ...

    async def bm25_search_chunks(
            self,
            kb_id: str,
            segmented_query: str,
            limit: int = 20,
    ) -> List[Tuple[KnowledgeChunk, KnowledgeDocument, float]]:
        ...

    async def get_parents_by_ids(self, parent_ids: List[str]) -> List[KnowledgeChunk]:
        ...

    async def get_chunks_by_ids(self, chunk_ids: List[str]) -> List[KnowledgeChunk]:
        ...

    async def list_chunks_for_document(
            self,
            doc_id: str,
            page_no: Optional[int] = None,
            limit: int = 20,
    ) -> List[KnowledgeChunk]:
        ...

    async def save_entities(self, entities: List[KnowledgeEntity]) -> None:
        ...

    async def save_relations(self, relations: List[KnowledgeRelation]) -> None:
        ...

    async def list_entities(self, kb_id: str, name: Optional[str] = None) -> List[KnowledgeEntity]:
        ...

    async def list_relations_for_entities(
            self,
            kb_id: str,
            entity_ids: List[str],
    ) -> List[KnowledgeRelation]:
        ...

    async def get_related_chunk_ids(self, kb_id: str, chunk_ids: List[str], limit: int = 20) -> List[str]:
        ...
