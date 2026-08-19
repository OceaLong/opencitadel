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


class ApprovalOutcomes(BaseModel):
    approved: int = 0
    rejected: int = 0
    expired: int = 0
    consumed: int = 0


class ApprovalStats(BaseModel):
    pending_count: int = 0
    avg_decision_seconds: Optional[float] = None
    outcomes: ApprovalOutcomes


class DailyCount(BaseModel):
    date: str
    approval_decisions: int = 0
    denials: int = 0


class DailyPatrolStat(BaseModel):
    date: str
    runs: int = 0
    findings: int = 0


class RemediationStatusCounts(BaseModel):
    proposed: int = 0
    executing: int = 0
    executed: int = 0
    verified: int = 0
    failed: int = 0
    cancelled: int = 0


class RemediationStats(BaseModel):
    by_status: RemediationStatusCounts
    success_rate: Optional[float] = None


class ChainStatus(BaseModel):
    ok: bool
    total: int
    first_broken_seq: Optional[int] = None
    checked_at: str


class GovernanceOverviewResponse(BaseModel):
    approvals: ApprovalStats
    interceptions: List[DailyCount] = Field(default_factory=list)
    patrol: List[DailyPatrolStat] = Field(default_factory=list)
    remediation: RemediationStats
    chain: ChainStatus
