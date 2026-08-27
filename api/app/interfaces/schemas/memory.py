from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.models.memory_entry import MemoryScope, MemorySource


class MemoryEntryCreateRequest(BaseModel):
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    scope: MemoryScope = MemoryScope.GLOBAL
    session_id: str | None = None


class MemoryEntryUpdateRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    scope: MemoryScope | None = None
    session_id: str | None = None


class MemoryEntryResponse(BaseModel):
    id: str
    scope: MemoryScope
    session_id: str | None
    title: str
    content: str
    tags: list[str]
    source: MemorySource
    last_used_at: datetime | None
    use_count: int
    created_at: datetime
    updated_at: datetime


class MemoryEntryListResponse(BaseModel):
    entries: list[MemoryEntryResponse]
