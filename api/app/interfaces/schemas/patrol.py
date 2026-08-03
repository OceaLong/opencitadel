"""HTTP contracts for owner-scoped Ops Patrol resources."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models.patrol import (
    PatrolCheckResult,
    PatrolFinding,
    PatrolFindingStatus,
    PatrolPack,
    PatrolPackConfig,
    PatrolRun,
)
from app.domain.utils.schedule_utils import compute_next_run


class CreatePatrolPackRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    mcp_server_id: str = Field(min_length=1, max_length=255)
    template_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class UpdatePatrolPackRequest(BaseModel):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    mcp_server_id: str | None = Field(default=None, min_length=1, max_length=255)
    config: PatrolPackConfig | None = None


class FindingDecisionRequest(BaseModel):
    reason: str = Field(default="", max_length=4000)


class PatrolPackResponse(BaseModel):
    id: str
    owner_user_id: str
    team_id: str | None
    name: str
    slug: str
    status: str
    version: int
    config: PatrolPackConfig
    mcp_server_id: str
    skill_id: str
    scheduled_job_id: str | None
    last_validated_at: datetime | None
    last_validated_version: int | None
    validation_summary: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    next_run_at: datetime | None = None

    @classmethod
    def from_domain(cls, pack: PatrolPack) -> "PatrolPackResponse":
        payload = pack.model_dump(mode="json")
        if pack.status.value == "active" and pack.config.schedule.enabled:
            payload["next_run_at"] = compute_next_run(
                "cron",
                pack.config.schedule.cron,
                timezone_name=pack.config.timezone,
            )
        return cls.model_validate(payload)


class PatrolPackListResponse(BaseModel):
    items: list[PatrolPackResponse]
    limit: int
    offset: int


class PatrolRunResponse(BaseModel):
    id: str
    pack_id: str
    pack_version: int
    session_id: str | None
    status: str
    trigger_type: str
    started_at: datetime | None
    finished_at: datetime | None
    first_reviewed_at: datetime | None
    duration_ms: int | None
    counts: dict[str, int]
    evidence_completeness: float | None
    summary: dict[str, Any]
    report_artifact_id: str | None
    created_at: datetime

    @classmethod
    def from_domain(cls, run: PatrolRun) -> "PatrolRunResponse":
        return cls(
            id=run.id,
            pack_id=run.pack_id,
            pack_version=run.pack_version,
            session_id=run.session_id,
            status=run.status.value,
            trigger_type=run.trigger_type.value,
            started_at=run.started_at,
            finished_at=run.finished_at,
            first_reviewed_at=run.first_reviewed_at,
            duration_ms=run.duration_ms,
            counts={
                "pass": run.pass_count,
                "warn": run.warn_count,
                "fail": run.fail_count,
                "error": run.error_count,
                "skipped": run.skipped_count,
            },
            evidence_completeness=run.evidence_completeness,
            summary=run.summary,
            report_artifact_id=run.report_artifact_id,
            created_at=run.created_at,
        )


class PatrolPackMetricsResponse(BaseModel):
    window_days: int = 30
    sample_size: int
    scheduled_run_count: int
    scheduled_success_rate: float | None
    finding_count: int
    false_positive_count: int
    median_review_minutes: float | None


class PatrolCheckResultResponse(BaseModel):
    id: str
    run_id: str
    check_id: str
    status: str
    severity: str
    observed: dict[str, Any]
    assertion_results: list[dict[str, Any]]
    evidence_refs: list[dict[str, Any]]
    explanation: str
    error_code: str | None
    error_message: str | None
    fingerprint: str
    started_at: datetime
    finished_at: datetime

    @classmethod
    def from_domain(cls, result: PatrolCheckResult) -> "PatrolCheckResultResponse":
        return cls.model_validate(result.model_dump(mode="json"))


class PatrolFindingResponse(BaseModel):
    id: str
    run_id: str
    check_result_id: str
    fingerprint: str
    severity: str
    status: str
    title: str
    summary: str
    first_seen_at: datetime
    last_seen_at: datetime
    occurrence_count: int
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None

    @classmethod
    def from_domain(cls, finding: PatrolFinding) -> "PatrolFindingResponse":
        return cls.model_validate(finding.model_dump(mode="json"))


class PatrolRunDetailResponse(PatrolRunResponse):
    check_results: list[PatrolCheckResultResponse] = Field(default_factory=list)
    findings: list[PatrolFindingResponse] = Field(default_factory=list)


class PatrolRunListResponse(BaseModel):
    items: list[PatrolRunResponse]
    limit: int
    offset: int
