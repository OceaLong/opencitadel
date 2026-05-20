#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.domain.models.memory_entry import MemoryScope, MemorySource


class MemoryEntryCreateRequest(BaseModel):
    title: str
    content: str
    tags: List[str] = Field(default_factory=list)
    scope: MemoryScope = MemoryScope.GLOBAL
    session_id: Optional[str] = None


class MemoryEntryUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[List[str]] = None
    scope: Optional[MemoryScope] = None
    session_id: Optional[str] = None


class MemoryEntryResponse(BaseModel):
    id: str
    scope: MemoryScope
    session_id: Optional[str]
    title: str
    content: str
    tags: List[str]
    source: MemorySource
    last_used_at: Optional[datetime]
    use_count: int
    created_at: datetime
    updated_at: datetime


class MemoryEntryListResponse(BaseModel):
    entries: List[MemoryEntryResponse]


class SessionMemoryResponse(BaseModel):
    planner: List[dict] = Field(default_factory=list)
    react: List[dict] = Field(default_factory=list)


class ClearMemoryRequest(BaseModel):
    agent_name: str
