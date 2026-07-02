#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ArtifactResponse(BaseModel):
    id: str
    session_id: str
    kind: Literal["doc", "web"]
    title: str
    storage_ref: str
    version_refs: List[str]
    status: Literal["draft", "updated", "final"]
    created_at: datetime
    updated_at: datetime


class ArtifactListResponse(BaseModel):
    artifacts: List[ArtifactResponse]


class ArtifactShareResponse(BaseModel):
    share_token: str
    share_url: str


class ArtifactContentResponse(BaseModel):
    content: str
    content_type: str
