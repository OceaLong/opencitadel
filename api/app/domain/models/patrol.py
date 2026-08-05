"""Domain contracts for deterministic, read-only Ops Patrol runs."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Pydantic's recursive JSON alias can exceed schema recursion limits when reused
# throughout nested Pack models. JSON-serializability is enforced at API/storage
# boundaries; the domain keeps values opaque for deterministic comparisons.
JsonValue = Any

PATROL_PROBE_TOOLS = frozenset(
    {
        "k8s_workload_summary",
        "k8s_recent_events",
        "k8s_pod_logs",
        "prom_query",
        "http_probe",
        "certificate_status",
        "backup_status",
        "dependency_status",
    }
)
EVIDENCE_TYPES = frozenset(
    {
        "summary",
        "resource_refs",
        "raw_probe_response",
        "log_excerpt",
        "metric_sample",
        "tls_chain",
        "backup_metadata",
        "collector_result",
    }
)
_SAFE_FIELD = re.compile(r"^\$\.[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*$")
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHECK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PatrolPackStatus(str, Enum):
    DRAFT = "draft"
    VALIDATING = "validating"
    ACTIVE = "active"
    PAUSED = "paused"
    INVALID = "invalid"


class PatrolRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_FINDINGS = "completed_with_findings"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PatrolCheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"


class PatrolFindingStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"


class PatrolFindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class PatrolTriggerType(str, Enum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    REPLAY = "replay"
    REMEDIATION = "remediation"  # Auto-triggered recheck run after a remediation executes.


class PatrolProbeStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    UNAVAILABLE = "unavailable"
    DENIED = "denied"


class PatrolSchedule(BaseModel):
    cron: str = "0 1 * * *"
    enabled: bool = False

    @field_validator("cron")
    @classmethod
    def validate_daily_cron(cls, value: str) -> str:
        parts = value.strip().split()
        if len(parts) != 5 or parts[2:] != ["*", "*", "*"]:
            raise ValueError("P0 only supports daily 'minute hour * * *' cron")
        try:
            minute, hour = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise ValueError("cron minute and hour must be integers") from exc
        if not 0 <= minute <= 59 or not 0 <= hour <= 23:
            raise ValueError("cron minute/hour out of range")
        return f"{minute} {hour} * * *"


class PatrolScope(BaseModel):
    cluster: str = Field(min_length=1, max_length=255)
    namespaces: list[str] = Field(min_length=1, max_length=50)
    environment: Literal["dev", "staging", "production"]

    @field_validator("namespaces")
    @classmethod
    def unique_namespaces(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValueError("namespaces must be non-empty and unique")
        return cleaned


class PatrolDefaults(BaseModel):
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    run_timeout_seconds: int = Field(default=900, ge=1, le=1800)
    max_evidence_items: int = Field(default=10, ge=1, le=50)


class PatrolProbe(BaseModel):
    tool: Literal[
        "k8s_workload_summary",
        "k8s_recent_events",
        "k8s_pod_logs",
        "prom_query",
        "http_probe",
        "certificate_status",
        "backup_status",
        "dependency_status",
    ]
    args: dict[str, JsonValue] = Field(default_factory=dict)
    output_schema_hash: str = Field(min_length=1, max_length=128)


class PatrolAssertion(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    field: str
    op: Literal[
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
        "not_in",
        "contains",
        "regex",
        "empty",
        "not_empty",
        "age_lt_seconds",
        "age_lte_seconds",
    ]
    value: JsonValue = None
    status_on_failure: Literal["warn", "fail"] = "fail"
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("field")
    @classmethod
    def validate_safe_field(cls, value: str) -> str:
        if not _SAFE_FIELD.fullmatch(value):
            raise ValueError("field must use the safe $.a.b subset")
        return value

    @model_validator(mode="after")
    def validate_operator_value(self) -> "PatrolAssertion":
        if self.op == "regex":
            if not isinstance(self.value, str):
                raise ValueError("regex requires a string value")
            if len(self.value) > 256:
                raise ValueError("regex is limited to 256 characters")
            if "(?" in self.value or re.search(r"\\[1-9]", self.value):
                raise ValueError("regex lookaround/backreferences are not allowed")
            re.compile(self.value)
        return self


class PatrolCheck(BaseModel):
    id: str
    title: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    probe: PatrolProbe
    assertions: list[PatrolAssertion] = Field(min_length=1)
    severity_on_fail: Literal["warning", "critical"]
    required_evidence: list[str] = Field(default_factory=list, max_length=20)
    runbook_ref: str | None = Field(default=None, max_length=1000)

    @field_validator("id")
    @classmethod
    def validate_check_id(cls, value: str) -> str:
        if not _CHECK_ID.fullmatch(value):
            raise ValueError("check id must be a lowercase kebab-case identifier")
        return value

    @field_validator("required_evidence")
    @classmethod
    def validate_evidence_types(cls, value: list[str]) -> list[str]:
        unknown = set(value) - EVIDENCE_TYPES
        if unknown:
            raise ValueError(f"unsupported evidence types: {sorted(unknown)}")
        if len(set(value)) != len(value):
            raise ValueError("required evidence types must be unique")
        return value

    @model_validator(mode="after")
    def validate_assertions(self) -> "PatrolCheck":
        ids = [item.id for item in self.assertions]
        if len(ids) != len(set(ids)):
            raise ValueError("assertion ids must be unique per check")
        return self


class PatrolPackConfig(BaseModel):
    schema_version: Literal[1] = 1
    target_ref: str = Field(min_length=1, max_length=255)
    timezone: str = "UTC"
    schedule: PatrolSchedule = Field(default_factory=PatrolSchedule)
    scope: PatrolScope
    defaults: PatrolDefaults = Field(default_factory=PatrolDefaults)
    checks: list[PatrolCheck] = Field(min_length=1, max_length=100)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA name") from exc
        return value

    @model_validator(mode="after")
    def validate_checks(self) -> "PatrolPackConfig":
        ids = [check.id for check in self.checks]
        if len(ids) != len(set(ids)):
            raise ValueError("check ids must be unique")
        if not any(check.enabled for check in self.checks):
            raise ValueError("at least one check must be enabled")
        return self


class PatrolEvidenceRef(BaseModel):
    type: str
    ref: str = Field(min_length=1, max_length=2000)
    sha256: str
    target_ref: str | None = Field(default=None, max_length=255)
    expires_at: datetime | None = None
    # Internal verification state. It is excluded from all serialized contracts
    # and is set only by the server-side evidence verifier/finalizer.
    verified: bool = Field(default=False, exclude=True)

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if value not in EVIDENCE_TYPES:
            raise ValueError("unsupported evidence type")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.lower()
        if not _HEX_SHA256.fullmatch(normalized):
            raise ValueError("sha256 must contain 64 lowercase hex characters")
        return normalized


class PatrolObservationSubmission(BaseModel):
    check_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    probe_status: PatrolProbeStatus = PatrolProbeStatus.OK
    observation: dict[str, JsonValue] = Field(default_factory=dict)
    evidence_refs: list[PatrolEvidenceRef] = Field(default_factory=list, max_length=50)
    explanation: str = Field(default="", max_length=4000)
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=2000)
    agent_status: str | None = Field(default=None, exclude=True)

    @field_validator("observation")
    @classmethod
    def bound_observation(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 65_536:
            raise ValueError("observation exceeds 65536 bytes")
        return value


class PatrolAssertionResult(BaseModel):
    assertion_id: str
    field: str
    op: str
    expected: JsonValue = None
    observed: JsonValue = None
    passed: bool
    status_on_failure: Literal["warn", "fail"]
    message: str


class PatrolEvaluatedCheck(BaseModel):
    check_id: str
    status: PatrolCheckStatus
    severity: PatrolFindingSeverity
    observed: dict[str, JsonValue] = Field(default_factory=dict)
    assertion_results: list[PatrolAssertionResult] = Field(default_factory=list)
    evidence_refs: list[PatrolEvidenceRef] = Field(default_factory=list)
    explanation: str = ""
    error_code: str | None = None
    error_message: str | None = None
    evidence_complete: bool = False


class PatrolPack(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    owner_user_id: str
    team_id: str | None = None
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)
    status: PatrolPackStatus = PatrolPackStatus.DRAFT
    version: int = Field(default=1, ge=1)
    config: PatrolPackConfig
    mcp_server_id: str
    skill_id: str
    scheduled_job_id: str | None = None
    last_validated_at: datetime | None = None
    last_validated_version: int | None = None
    validation_summary: dict[str, Any] = Field(default_factory=dict)
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PatrolRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    pack_id: str
    session_id: str | None = None
    pack_version: int = Field(ge=1)
    pack_snapshot: dict[str, Any]
    trigger_type: PatrolTriggerType
    status: PatrolRunStatus = PatrolRunStatus.QUEUED
    idempotency_key: str = Field(min_length=1, max_length=255)
    submission_idempotency_key: str = Field(default="", max_length=255)
    collector_capability_hash: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    first_reviewed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    pass_count: int = Field(default=0, ge=0)
    warn_count: int = Field(default=0, ge=0)
    fail_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    evidence_completeness: float | None = Field(default=None, ge=0, le=1)
    summary: dict[str, Any] = Field(default_factory=dict)
    report_artifact_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PatrolCheckResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    check_id: str
    status: PatrolCheckStatus
    severity: PatrolFindingSeverity
    observed: dict[str, JsonValue] = Field(default_factory=dict)
    assertion_results: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    explanation: str = Field(default="", max_length=4000)
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=2000)
    fingerprint: str
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)


class PatrolFinding(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    check_result_id: str
    fingerprint: str
    severity: PatrolFindingSeverity
    status: PatrolFindingStatus = PatrolFindingStatus.OPEN
    title: str = Field(max_length=255)
    summary: str = Field(max_length=4000)
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    occurrence_count: int = Field(default=1, ge=1)
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def require_false_positive_reason(self) -> "PatrolFinding":
        if self.status == PatrolFindingStatus.FALSE_POSITIVE and not (self.decision_reason or "").strip():
            raise ValueError("false-positive decisions require a reason")
        return self


def patrol_fingerprint(*parts: str) -> str:
    canonical = json.dumps(list(parts), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PatrolRemediationAction(str, Enum):
    RESTART_WORKLOAD = "restart_workload"
    SCALE_WORKLOAD = "scale_workload"
    ROLLBACK_WORKLOAD = "rollback_workload"


class PatrolRemediationStatus(str, Enum):
    PROPOSED = "proposed"  # 提案已建，修复会话未批
    EXECUTING = "executing"  # 审批通过，执行中
    EXECUTED = "executed"  # actuator 已应用，待复检
    VERIFIED = "verified"  # 复检通过，Finding 已 resolved
    FAILED = "failed"  # 执行失败或复检未过
    CANCELLED = "cancelled"  # 会话被拒/取消


# Remediations in these statuses are done: a Finding may accept a new proposal.
# Every other status is treated as "in flight" and blocks a second concurrent
# proposal for the same Finding (enforced by both the service and a DB partial
# unique index — see alembic revision that creates patrol_remediations).
PATROL_REMEDIATION_TERMINAL_STATUSES = frozenset(
    {
        PatrolRemediationStatus.VERIFIED,
        PatrolRemediationStatus.FAILED,
        PatrolRemediationStatus.CANCELLED,
    }
)


class PatrolRemediation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    pack_id: str
    run_id: str
    finding_id: str
    check_result_id: str
    fingerprint: str
    session_id: str | None = None
    action: PatrolRemediationAction
    target_namespace: str = Field(min_length=1, max_length=255)
    target_workload: str = Field(default="", max_length=255)
    target_kind: str = Field(default="Deployment", max_length=64)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    params_hash: str
    impact_summary: str = Field(default="", max_length=2000)
    rollback_hint: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=255)
    actuator_capability_hash: str | None = None
    status: PatrolRemediationStatus = PatrolRemediationStatus.PROPOSED
    before_observation: dict[str, JsonValue] | None = None
    after_observation: dict[str, JsonValue] | None = None
    recheck_run_id: str | None = None
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=2000)
    created_by: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


def patrol_remediation_params_hash(
    action: str,
    namespace: str,
    workload: str,
    kind: str,
    params: dict[str, JsonValue],
) -> str:
    canonical = json.dumps(
        {"action": action, "namespace": namespace, "workload": workload, "kind": kind, "params": params},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
