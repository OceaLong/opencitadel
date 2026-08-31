import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditLog(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    actor_user_id: str | None = None
    actor_ip: str = ""
    action: str
    resource_type: str = ""
    resource_id: str = ""
    team_id: str | None = None
    session_id: str | None = None
    request_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    chain_seq: int | None = None
    signing_key_id: str = ""
    prev_hash: str = ""
    entry_hash: str = ""
