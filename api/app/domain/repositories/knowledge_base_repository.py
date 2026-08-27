from dataclasses import dataclass
from typing import Protocol

from app.domain.models.inference import PLATFORM_EMBEDDING_DIMENSIONS
from app.domain.models.knowledge_base import (
    DocStatus,
    KBStatus,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEntity,
    KnowledgeEntityRef,
    KnowledgeRelation,
)
from app.domain.models.scope import OwnerScope
from app.domain.repositories.patch import UNSET, UnsetType

KNOWLEDGE_EMBEDDING_DIMENSION = PLATFORM_EMBEDDING_DIMENSIONS


@dataclass(frozen=True)
class VersionedKnowledgeChunk:
    """One chunk closed over its exact published manifest revision."""

    chunk: KnowledgeChunk
    document: KnowledgeDocument
    document_revision_id: str
    score: float = 0.0


@dataclass(frozen=True)
class DocumentPageItem:
    """Frozen scalar snapshot of one parent source chunk."""

    id: str
    page_no: int | None
    heading_path: str
    ordinal: int
    content: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.id, str)
            or not isinstance(self.heading_path, str)
            or not isinstance(self.content, str)
            or not isinstance(self.ordinal, int)
            or isinstance(self.ordinal, bool)
            or (
                self.page_no is not None
                and (not isinstance(self.page_no, int) or isinstance(self.page_no, bool))
            )
        ):
            raise TypeError("document page items must contain scalar values")
        if not self.id or (self.page_no is not None and self.page_no < 1) or self.ordinal < 0:
            raise ValueError("document page item identity and ordering values are invalid")

    @classmethod
    def from_chunk(cls, chunk: KnowledgeChunk) -> "DocumentPageItem":
        return cls(
            id=chunk.id,
            page_no=chunk.page_no,
            heading_path=chunk.heading_path,
            ordinal=chunk.ordinal,
            content=chunk.content,
        )


@dataclass(frozen=True)
class DocumentPage:
    """One immutable page of parent source chunk snapshots."""

    items: tuple[DocumentPageItem, ...]
    next_cursor: str | None
    total: int
    truncated: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "items",
            tuple(
                item if isinstance(item, DocumentPageItem) else DocumentPageItem.from_chunk(item)
                for item in self.items
            ),
        )
        if self.total < 0 or self.total < len(self.items):
            raise ValueError("document page total cannot be smaller than items")
        if self.truncated != (self.next_cursor is not None):
            raise ValueError("document page truncated must match next_cursor presence")


class KnowledgeBaseRepository(Protocol):
    async def save_kb(self, kb: KnowledgeBase) -> None: ...

    async def get_kb(self, kb_id: str, scope: OwnerScope | None = None) -> KnowledgeBase | None: ...

    async def get_kb_for_update(
        self,
        kb_id: str,
        scope: OwnerScope | None = None,
    ) -> KnowledgeBase | None:
        """Return and row-lock one owner-scoped KB for a write decision."""
        ...

    async def list_kbs(
        self, limit: int = 100, offset: int = 0, scope: OwnerScope | None = None
    ) -> list[KnowledgeBase]: ...

    async def delete_kb(self, kb_id: str) -> None: ...

    async def update_status(
        self,
        kb_id: str,
        status: KBStatus,
        error: str | None = None,
    ) -> None: ...

    async def save_document(self, document: KnowledgeDocument) -> None: ...

    async def insert_document(self, document: KnowledgeDocument) -> None:
        """Insert a new logical document without updating an existing row."""
        ...

    async def list_documents(self, kb_id: str) -> list[KnowledgeDocument]: ...

    async def get_document(self, doc_id: str) -> KnowledgeDocument | None: ...

    async def get_document_for_build(
        self,
        doc_id: str,
    ) -> KnowledgeDocument | None:
        """Internal raw logical-document lookup for candidate construction."""
        ...

    async def delete_document(self, doc_id: str) -> None: ...

    async def count_documents(self, kb_id: str) -> int: ...

    async def update_document_status(
        self,
        doc_id: str,
        status: DocStatus,
        error: str | UnsetType | None = UNSET,
        warning: str | UnsetType | None = UNSET,
        page_count: int | None = None,
    ) -> None: ...

    async def clear_index_data(self, kb_id: str) -> None: ...

    async def replace_index_chunks(self, kb_id: str, chunks: list[KnowledgeChunk]) -> None:
        """Atomically replace all chunk index data for a knowledge base."""
        ...

    async def save_chunks(self, chunks: list[KnowledgeChunk]) -> None: ...

    async def replace_candidate_chunks(
        self,
        kb_id: str,
        version_id: str,
        chunks: list[KnowledgeChunk],
    ) -> None:
        """Replace only one building candidate's chunk rows."""
        ...

    async def replace_candidate_graph(
        self,
        kb_id: str,
        version_id: str,
        entities: list[KnowledgeEntity],
        relations: list[KnowledgeRelation],
        refs: list[KnowledgeEntityRef],
    ) -> None:
        """Replace only one building candidate's graph rows."""
        ...

    async def upsert_candidate_graph_batch(
        self,
        kb_id: str,
        version_id: str,
        entities: list[KnowledgeEntity],
        relations: list[KnowledgeRelation],
        refs: list[KnowledgeEntityRef],
    ) -> None:
        """Atomically merge one retry-safe graph extraction batch."""
        ...

    async def get_candidate_index_metrics(
        self,
        kb_id: str,
        version_id: str,
    ) -> dict[str, int]:
        """Validate same-version closure and return committed row counts."""
        ...

    async def vector_search_chunks(
        self,
        kb_id: str,
        query_embedding: list[float],
        limit: int = 20,
    ) -> list[tuple[KnowledgeChunk, KnowledgeDocument, float]]: ...

    async def bm25_search_chunks(
        self,
        kb_id: str,
        segmented_query: str,
        limit: int = 20,
    ) -> list[tuple[KnowledgeChunk, KnowledgeDocument, float]]: ...

    async def vector_search_chunks_for_version(
        self,
        kb_id: str,
        version_id: str,
        query_embedding: list[float],
        limit: int = 20,
    ) -> list[VersionedKnowledgeChunk]:
        """Search only one exact published version; never use active/NULL."""
        ...

    async def bm25_search_chunks_for_version(
        self,
        kb_id: str,
        version_id: str,
        segmented_query: str,
        limit: int = 20,
    ) -> list[VersionedKnowledgeChunk]:
        """Search mandatory keyword rows in one exact published version."""
        ...

    async def get_parents_by_ids_for_version(
        self,
        kb_id: str,
        version_id: str,
        parent_ids: list[str],
    ) -> list[KnowledgeChunk]: ...

    async def get_chunks_by_ids_for_version(
        self,
        kb_id: str,
        version_id: str,
        chunk_ids: list[str],
    ) -> list[VersionedKnowledgeChunk]: ...

    async def get_document_for_version(
        self,
        kb_id: str,
        version_id: str,
        doc_id: str,
    ) -> tuple[KnowledgeDocument, str] | None:
        """Return the logical document plus exact manifest revision."""
        ...

    async def list_chunks_for_document_for_version(
        self,
        kb_id: str,
        version_id: str,
        doc_id: str,
        page_no: int | None = None,
        limit: int = 20,
    ) -> list[KnowledgeChunk]: ...

    async def read_document_page_for_version(
        self,
        kb_id: str,
        version_id: str,
        doc_id: str,
        document_revision_id: str,
        *,
        page_no: int | None = None,
        cursor: str | None = None,
        limit: int = 30,
    ) -> DocumentPage:
        """Read parent chunks from one exact published manifest revision."""
        ...

    async def get_parents_by_ids(self, parent_ids: list[str]) -> list[KnowledgeChunk]: ...

    async def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[KnowledgeChunk]: ...

    async def list_chunks_for_document(
        self,
        doc_id: str,
        page_no: int | None = None,
        limit: int = 20,
    ) -> list[KnowledgeChunk]: ...

    async def purge_documents_index_data(self, doc_ids: list[str]) -> None: ...

    async def count_ready_documents(self, kb_ids: list[str]) -> dict[str, int]: ...

    async def count_child_chunks(self, kb_id: str) -> int: ...

    async def list_documents_page(
        self,
        kb_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[KnowledgeDocument], int]: ...

    async def mark_documents_pending(self, kb_id: str) -> None: ...

    async def list_entities(self, kb_id: str, name: str | None = None) -> list[KnowledgeEntity]: ...

    async def list_relations_for_entities(
        self,
        kb_id: str,
        entity_ids: list[str],
    ) -> list[KnowledgeRelation]: ...

    async def get_related_chunk_ids(
        self, kb_id: str, chunk_ids: list[str], limit: int = 20
    ) -> list[str]: ...

    async def list_entities_for_version(
        self,
        kb_id: str,
        version_id: str,
        name: str | None = None,
    ) -> list[KnowledgeEntity]: ...

    async def list_entities_page_for_version(
        self,
        kb_id: str,
        version_id: str,
        *,
        q: str | None,
        after: tuple[str, str] | None,
        limit: int,
    ) -> tuple[list[KnowledgeEntity], tuple[str, str] | None]:
        """Return a deterministic keyset page from one published graph."""
        ...

    async def get_entities_by_ids_for_version(
        self,
        kb_id: str,
        version_id: str,
        entity_ids: list[str],
    ) -> list[KnowledgeEntity]:
        """Resolve exact-version relation endpoints."""
        ...

    async def list_relations_for_entities_for_version(
        self,
        kb_id: str,
        version_id: str,
        entity_ids: list[str],
    ) -> list[KnowledgeRelation]: ...

    async def get_related_chunk_ids_for_version(
        self,
        kb_id: str,
        version_id: str,
        chunk_ids: list[str],
        limit: int = 20,
    ) -> list[str]: ...
