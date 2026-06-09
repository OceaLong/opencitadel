#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CheckpointAnchorType(str, Enum):
    """Checkpoint anchor type."""
    USER_MESSAGE = "user_message"
    STEP = "step"


class SessionStateSnapshot(BaseModel):
    """Session metadata captured at checkpoint time."""
    status: str = "pending"
    pending_phase: Optional[str] = None


class Checkpoint(BaseModel):
    """Domain model for a session checkpoint."""
    id: str
    session_id: str
    anchor_type: CheckpointAnchorType
    anchor_event_id: str
    label: str = ""
    seq_before: Optional[int] = None
    memories_snapshot: Dict[str, Any] = Field(default_factory=dict)
    files_snapshot: List[Dict[str, Any]] = Field(default_factory=list)
    session_state: SessionStateSnapshot = Field(default_factory=SessionStateSnapshot)
    sandbox_snapshot_key: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
