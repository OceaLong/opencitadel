from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ArtifactResponse(BaseModel):
    id: str
    session_id: str
    kind: Literal["doc", "web"]
    title: str
    storage_ref: str
    version_refs: list[str]
    status: Literal["draft", "updated", "final"]
    created_at: datetime
    updated_at: datetime


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactResponse]


class ArtifactShareResponse(BaseModel):
    share_token: str
    share_url: str


class ArtifactContentResponse(BaseModel):
    content: str
    content_type: str
    incomplete: bool = False
