#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import List, Optional

from app.domain.models.memory_entry import MemoryEntry, MemoryScope
from app.domain.models.scope import OwnerScope


class MemoryEntryRepository(ABC):
    @abstractmethod
    async def get_all(
            self,
            scope: Optional[MemoryScope] = None,
            session_id: Optional[str] = None,
            q: Optional[str] = None,
            tags: Optional[List[str]] = None,
            limit: int = 100,
            owner_scope: Optional[OwnerScope] = None,
    ) -> List[MemoryEntry]:
        ...

    @abstractmethod
    async def get_by_id(self, entry_id: str, owner_scope: Optional[OwnerScope] = None) -> Optional[MemoryEntry]:
        ...

    @abstractmethod
    async def recall_for_session(self, session_id: str, limit: int = 20) -> List[MemoryEntry]:
        ...

    @abstractmethod
    async def save(self, entry: MemoryEntry) -> None:
        ...

    @abstractmethod
    async def delete_by_id(self, entry_id: str, owner_scope: Optional[OwnerScope] = None) -> None:
        ...

    @abstractmethod
    async def touch_used(self, entry_ids: List[str]) -> None:
        ...
