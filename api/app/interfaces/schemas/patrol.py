"""HTTP contracts for owner-scoped Ops Patrol resources."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models.patrol import (
    PatrolCheckResult,
    PatrolCheckStatus,
    PatrolFinding,
    PatrolFindingSeverity,
    PatrolFindingStatus,
    PatrolPack,
    PatrolPackConfig,
    PatrolPackStatus,
    PatrolRemediation,
    PatrolRemediationAction,
    PatrolRemediationStatus,
    PatrolRun,
    PatrolRunStatus,
    PatrolTriggerType,
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
    status: PatrolPackStatus
    version: int
    config: PatrolPackConfig
    mcp_server_id: str
    scheduled_job_id: str | None
    last_validated_at: datetime | None
    last_validated_version: int | None
    validation_summary: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    next_run_at: datetime | None = None

    @classmethod
    def from_domain(cls, pack: PatrolPack) -> PatrolPackResponse:
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
    status: PatrolRunStatus
    trigger_type: PatrolTriggerType
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
    def from_domain(cls, run: PatrolRun) -> PatrolRunResponse:
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
    status: PatrolCheckStatus
    severity: PatrolFindingSeverity
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
    def from_domain(cls, result: PatrolCheckResult) -> PatrolCheckResultResponse:
        return cls.model_validate(result.model_dump(mode="json"))


class PatrolFindingResponse(BaseModel):
    id: str
    run_id: str
    check_result_id: str
    fingerprint: str
    severity: PatrolFindingSeverity
    status: PatrolFindingStatus
    title: str
    summary: str
    first_seen_at: datetime
    last_seen_at: datetime
    occurrence_count: int
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    # Computed, not persisted on the domain model: which remediation actions
    # the Finding's originating Check's probe tool supports (source of truth
    # is patrol_remediation_service._allowed_actions_for_probe_tool — see
    # app/interfaces/endpoints/patrol_routes.py::_finding_allowed_actions for
    # the assembly point). Lets the UI stop mirroring that rule client-side.
    allowed_actions: list[PatrolRemediationAction]

    @classmethod
    def from_domain(
        cls,
        finding: PatrolFinding,
        *,
        allowed_actions: list[PatrolRemediationAction | str],
    ) -> PatrolFindingResponse:
        payload = finding.model_dump(mode="json")
        payload["allowed_actions"] = list(allowed_actions)
        return cls.model_validate(payload)


class PatrolRunDetailResponse(PatrolRunResponse):
    check_results: list[PatrolCheckResultResponse] = Field(default_factory=list)
    findings: list[PatrolFindingResponse] = Field(default_factory=list)


class PatrolRunListResponse(BaseModel):
    items: list[PatrolRunResponse]
    limit: int
    offset: int


class ProposePatrolRemediationRequest(BaseModel):
    action: PatrolRemediationAction
    params: dict[str, Any] = Field(default_factory=dict)
    # Optional explicit target when the probe does not identify a workload.
    workload: str | None = Field(default=None, min_length=1, max_length=255)


class PatrolRemediationResponse(BaseModel):
    id: str
    pack_id: str
    run_id: str
    finding_id: str
    check_result_id: str
    fingerprint: str
    session_id: str | None
    action: PatrolRemediationAction
    target_namespace: str
    target_workload: str
    target_kind: str
    params: dict[str, Any]
    params_hash: str
    impact_summary: str
    rollback_hint: str
    idempotency_key: str
    actuator_capability_hash: str | None
    status: PatrolRemediationStatus
    before_observation: dict[str, Any] | None
    after_observation: dict[str, Any] | None
    recheck_run_id: str | None
    error_code: str | None
    error_message: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, remediation: PatrolRemediation) -> PatrolRemediationResponse:
        return cls.model_validate(remediation.model_dump(mode="json"))


class PatrolRemediationListResponse(BaseModel):
    items: list[PatrolRemediationResponse]
