#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List

from pydantic import BaseModel

from app.domain.models.checkpoint import CheckpointAnchorType


class CheckpointItemResponse(BaseModel):
    """Checkpoint list item."""
    id: str
    session_id: str
    anchor_type: CheckpointAnchorType
    anchor_event_id: str
    label: str
    created_at: datetime


class ListCheckpointsResponse(BaseModel):
    """Checkpoint list response."""
    checkpoints: List[CheckpointItemResponse]


class RestoreCheckpointResponse(BaseModel):
    """Restore checkpoint response."""
    success: bool = True
    message: str = "已回退到指定还原点"
