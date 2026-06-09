#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class FortunePrediction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    share_id: str = Field(default_factory=lambda: str(uuid4()))
    mode: str
    question: str
    input_profile: Dict[str, Any] = Field(default_factory=dict)
    result: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
