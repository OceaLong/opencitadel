from typing import Any

from pydantic import BaseModel, Field

from app.domain.models.session import SessionStatus


class ChainVerifyResponse(BaseModel):
    ok: bool
    total: int
    first_broken_seq: int | None = None
    checked_at: str
    session_id: str | None = None
    session_entries: int | None = None
    session_ok: bool | None = None
    session_first_broken_seq: int | None = None


class EvidenceSessionItem(BaseModel):
    session_id: str
    title: str
    owner_user_id: str | None
    team_id: str | None
    operator_scope: str | None = None
    status: SessionStatus
    updated_at: str | None = None
    chain_ok: bool = False
    tool_invocation_count: int = 0
    governance_action_count: int = 0


class EvidenceSessionListResponse(BaseModel):
    sessions: list[EvidenceSessionItem] = Field(default_factory=list)


class ComplianceReportResponse(BaseModel):
    report: dict[str, Any]


class ApprovalOutcomes(BaseModel):
    approved: int = 0
    rejected: int = 0
    cancelled: int = 0


class ApprovalStats(BaseModel):
    pending_count: int = 0
    avg_decision_seconds: float | None = None
    outcomes: ApprovalOutcomes


class DailyCount(BaseModel):
    date: str
    approval_requests: int = 0
    activity_failures: int = 0


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
    success_rate: float | None = None


class ChainStatus(BaseModel):
    ok: bool
    total: int
    first_broken_seq: int | None = None
    checked_at: str


class GovernanceOverviewResponse(BaseModel):
    approvals: ApprovalStats
    interceptions: list[DailyCount] = Field(default_factory=list)
    patrol: list[DailyPatrolStat] = Field(default_factory=list)
    remediation: RemediationStats
    chain: ChainStatus
