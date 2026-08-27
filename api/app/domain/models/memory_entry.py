import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.utils.time_utils import utc_now


class MemoryScope(StrEnum):
    """记忆作用域"""

    GLOBAL = "global"
    SESSION = "session"


class MemorySource(StrEnum):
    """记忆来源"""

    MANUAL = "manual"
    AUTO_EXTRACTED = "auto_extracted"
    TOOL_SAVE = "tool_save"


class MemoryEntry(BaseModel):
    """长期记忆条目领域模型"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    scope: MemoryScope = MemoryScope.GLOBAL
    session_id: str | None = None
    title: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    owner_user_id: str | None = None
    team_id: str | None = None
    source: MemorySource = MemorySource.MANUAL
    last_used_at: datetime | None = None
    use_count: int = 0
    vector_score: float | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
