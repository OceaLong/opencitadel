#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Auditable execution outcomes for governed tool calls."""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ToolExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class ToolExecutionAttempt(BaseModel):
    """A non-sensitive summary of one tool invocation attempt."""

    attempt_number: int = Field(ge=1)
    started_at: datetime
    duration_ms: int = Field(ge=0)
    status: ToolExecutionStatus
    result_summary: str = ""
    transient_failure: bool = False
    idempotency_key: Optional[str] = None
