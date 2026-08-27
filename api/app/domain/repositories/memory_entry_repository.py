from abc import ABC, abstractmethod

from app.domain.models.memory_entry import MemoryEntry, MemoryScope
from app.domain.models.scope import OwnerScope


class MemoryEntryRepository(ABC):
    @abstractmethod
    async def get_all(
        self,
        scope: MemoryScope | None = None,
        session_id: str | None = None,
        q: str | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
        owner_scope: OwnerScope | None = None,
    ) -> list[MemoryEntry]: ...

    @abstractmethod
    async def get_by_id(
        self, entry_id: str, owner_scope: OwnerScope | None = None
    ) -> MemoryEntry | None: ...

    @abstractmethod
    async def recall_for_session(self, session_id: str, limit: int = 20) -> list[MemoryEntry]: ...

    @abstractmethod
    async def save(self, entry: MemoryEntry) -> None: ...

    @abstractmethod
    async def delete_by_id(self, entry_id: str, owner_scope: OwnerScope | None = None) -> None: ...

    @abstractmethod
    async def touch_used(self, entry_ids: list[str]) -> None: ...

    @abstractmethod
    async def update_embedding(self, entry_id: str, embedding: list[float]) -> None: ...

    @abstractmethod
    async def vector_search_entries(
        self,
        query_embedding: list[float],
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[MemoryEntry]: ...
