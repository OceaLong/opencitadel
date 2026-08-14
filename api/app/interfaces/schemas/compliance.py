#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# Five-state compliance evaluator vocabulary (Phase A Task 4, spec §A5):
#   pass         判定满足
#   gap          判定不满足，硬失败（如证据链断裂）
#   attention    有数据但治理未被触发或未达标
#   not_verified 无法程序化验证，如实陈述依据缺失
#   na           evaluator 缺失
# ``ComplianceReport.controls[i].status`` and ``ComplianceReport.summary``
# use this vocabulary. ``ComplianceReportResponse.report`` stays a loose
# ``Dict[str, Any]`` (built by ComplianceService.build_report, not a pydantic
# model) so this alias documents the contract for callers/UI rather than
# enforcing it at the wire boundary.
ComplianceStatus = Literal["pass", "gap", "attention", "not_verified", "na"]


class ComplianceSummary(BaseModel):
    """Shape of ``ComplianceReport.summary`` -- documented for UI/consumer
    reference; see ``ComplianceStatus`` for the five-state vocabulary."""

    pass_: int = Field(0, alias="pass")
    gap: int = 0
    attention: int = 0
    not_verified: int = 0
    na: int = 0
    total: int = 0

    model_config = {"populate_by_name": True}


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
