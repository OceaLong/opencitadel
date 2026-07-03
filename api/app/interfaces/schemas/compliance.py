#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChainVerifyResponse(BaseModel):
    ok: bool
    total: int
    first_broken_seq: Optional[int] = None
    checked_at: str
    session_id: Optional[str] = None
    session_entries: Optional[int] = None
    session_ok: Optional[bool] = None
    session_first_broken_seq: Optional[int] = None


class EvidenceSessionItem(BaseModel):
    session_id: str
    title: str
    operator_scope: Optional[str] = None
    gate_profile: Optional[str] = None
    status: str
    updated_at: Optional[str] = None
    chain_ok: bool = False
    tool_invocation_count: int = 0
    governance_action_count: int = 0


class EvidenceSessionListResponse(BaseModel):
    sessions: List[EvidenceSessionItem] = Field(default_factory=list)


class ComplianceReportResponse(BaseModel):
    report: Dict[str, Any]
