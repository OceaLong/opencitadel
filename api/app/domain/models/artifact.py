import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.utils.time_utils import utc_now

ArtifactKind = Literal["doc", "web"]
ArtifactStatus = Literal["draft", "updated", "final"]


class Artifact(BaseModel):
    """Session delivery artifact metadata (content stored in COS)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    kind: ArtifactKind = "doc"
    title: str = ""
    storage_ref: str = ""
    version_refs: list[str] = Field(default_factory=list)
    status: ArtifactStatus = "draft"
    share_token: str | None = None
    share_expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
