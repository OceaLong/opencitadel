#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class MemoryScope(str, Enum):
    """记忆作用域"""
    GLOBAL = "global"
    SESSION = "session"


class MemorySource(str, Enum):
    """记忆来源"""
    MANUAL = "manual"
    AUTO_EXTRACTED = "auto_extracted"
    TOOL_SAVE = "tool_save"


class MemoryEntry(BaseModel):
    """长期记忆条目领域模型"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    scope: MemoryScope = MemoryScope.GLOBAL
    session_id: Optional[str] = None
    title: str = ""
    content: str = ""
    tags: List[str] = Field(default_factory=list)
    owner_user_id: Optional[str] = None
    team_id: Optional[str] = None
    source: MemorySource = MemorySource.MANUAL
    last_used_at: Optional[datetime] = None
    use_count: int = 0
    vector_score: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
