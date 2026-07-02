#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from typing import Literal, List, Optional

from pydantic import BaseModel, Field


ArtifactKind = Literal["doc", "web"]
ArtifactStatus = Literal["draft", "updated", "final"]


class Artifact(BaseModel):
    """Session delivery artifact metadata (content stored in COS)."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    kind: ArtifactKind = "doc"
    title: str = ""
    storage_ref: str = ""
    version_refs: List[str] = Field(default_factory=list)
    status: ArtifactStatus = "draft"
    share_token: Optional[str] = None
    share_expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
