"""Immutable revisions for live admission, safety, and maintenance policy."""

from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class _OperationsPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PatrolAdmissionMode(StrEnum):
    ACCEPTING = "accepting"
    PAUSED = "paused"


class PatrolRemediationMode(StrEnum):
    DISABLED = "disabled"
    PROPOSE_ONLY = "propose_only"
    ENABLED = "enabled"


class TrafficPolicy(_OperationsPolicyModel):
    rate_limit_enabled: bool = True
    requests_per_minute: int = Field(default=120, ge=1, le=100_000)
    session_stream_interval_seconds: int = Field(default=15, ge=1, le=3_600)


class SchedulerPolicy(_OperationsPolicyModel):
    enabled: bool = True
    poll_interval_seconds: float = Field(default=10.0, ge=0.1, le=3_600)
    max_concurrent_jobs: int = Field(default=5, ge=1, le=1_000)
    leader_lease_seconds: int = Field(default=30, ge=1, le=3_600)
    webhook_idempotency_ttl_seconds: int = Field(default=600, ge=1, le=604_800)


class PatrolOperationsPolicy(_OperationsPolicyModel):
    admission: PatrolAdmissionMode = PatrolAdmissionMode.ACCEPTING
    remediation: PatrolRemediationMode = PatrolRemediationMode.DISABLED


class SandboxOperationsPolicy(_OperationsPolicyModel):
    ttl_minutes: int = Field(default=60, ge=1, le=10_080)
    cleanup_interval_seconds: int = Field(default=300, ge=1, le=3_600)
    memory_limit: str = Field(default="2g", pattern=r"^[1-9][0-9]*[kKmMgG]$")
    cpu_limit: float = Field(default=2.0, ge=0.1, le=128)
    pids_limit: int = Field(default=512, ge=16, le=32_768)
    pool_enabled: bool = True
    pool_size: int = Field(default=2, ge=0, le=100)
    idle_timeout_minutes: int = Field(default=30, ge=1, le=1_440)
    warmup_retry_interval_seconds: float = Field(default=0.5, ge=0.05, le=60)
    warmup_max_retries: int = Field(default=30, ge=1, le=1_000)
    max_sandboxes_per_node: int = Field(default=4, ge=1, le=1_000)
    max_dynamic_sandboxes_global: int = Field(default=0, ge=0, le=100_000)
    admission_min_host_available_mb: int = Field(default=3_072, ge=0, le=1_048_576)
    admission_reclaim_target_mb: int = Field(default=4_096, ge=0, le=1_048_576)
    admission_poll_interval_seconds: float = Field(default=2.0, ge=0.05, le=300)
    admission_settle_seconds: float = Field(default=8.0, ge=0, le=3_600)
    admission_reclaim_enabled: bool = True
    reclaim_leader_lease_seconds: int = Field(default=15, ge=1, le=3_600)

    @model_validator(mode="after")
    def validate_pool_and_reclaim(self) -> "SandboxOperationsPolicy":
        if self.pool_enabled and self.pool_size < 1:
            raise ValueError("pool_size must be positive when pool_enabled is true")
        if (
            self.admission_reclaim_enabled
            and self.admission_reclaim_target_mb < self.admission_min_host_available_mb
        ):
            raise ValueError(
                "admission_reclaim_target_mb must be at least admission_min_host_available_mb"
            )
        return self


class ResourceVersionGcPolicy(_OperationsPolicyModel):
    enabled: bool = False
    retention_count: int = Field(default=10, ge=0, le=10_000)
    retention_min_days: int = Field(default=30, ge=0, le=36_500)
    batch_size: int = Field(default=50, ge=1, le=500)


class ResourceGcPolicy(_OperationsPolicyModel):
    knowledge_base: ResourceVersionGcPolicy = Field(default_factory=ResourceVersionGcPolicy)
    codebase: ResourceVersionGcPolicy = Field(default_factory=ResourceVersionGcPolicy)


class PatrolRetentionPolicy(_OperationsPolicyModel):
    run_days: int = Field(default=30, ge=1, le=90)
    finding_days: int = Field(default=30, ge=1, le=90)
    collector_evidence_days: int = Field(default=7, ge=1, le=90)
    cleanup_batch_size: int = Field(default=100, ge=1, le=1_000)


def _normalize_host_pattern(value: str) -> str:
    normalized = value.strip().lower().rstrip(".")
    if not normalized:
        raise ValueError("source access host patterns must not be empty")
    if "://" in normalized or "/" in normalized:
        raise ValueError("source access entries must be host names, not URLs")
    try:
        return normalized.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("source access host pattern is invalid") from exc


class SourceAccessPolicy(_OperationsPolicyModel):
    url_allowlist: tuple[str, ...] = ()
    url_denylist: tuple[str, ...] = ()

    @field_validator("url_allowlist", "url_denylist", mode="before")
    @classmethod
    def normalize_patterns(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise TypeError("source access lists must be arrays")
        return tuple(sorted({_normalize_host_pattern(str(item)) for item in value}))

    @model_validator(mode="after")
    def reject_overlap(self) -> "SourceAccessPolicy":
        overlap = set(self.url_allowlist).intersection(self.url_denylist)
        if overlap:
            raise ValueError("a source host cannot be both allowed and denied")
        return self


class OperationsPolicy(_OperationsPolicyModel):
    traffic: TrafficPolicy = Field(default_factory=TrafficPolicy)
    scheduler: SchedulerPolicy = Field(default_factory=SchedulerPolicy)
    patrol: PatrolOperationsPolicy = Field(default_factory=PatrolOperationsPolicy)
    sandbox: SandboxOperationsPolicy = Field(default_factory=SandboxOperationsPolicy)
    resource_gc: ResourceGcPolicy = Field(default_factory=ResourceGcPolicy)
    patrol_retention: PatrolRetentionPolicy = Field(default_factory=PatrolRetentionPolicy)
    source_access: SourceAccessPolicy = Field(default_factory=SourceAccessPolicy)


__all__ = [
    "OperationsPolicy",
    "PatrolAdmissionMode",
    "PatrolOperationsPolicy",
    "PatrolRemediationMode",
    "PatrolRetentionPolicy",
    "ResourceGcPolicy",
    "ResourceVersionGcPolicy",
    "SandboxOperationsPolicy",
    "SchedulerPolicy",
    "SourceAccessPolicy",
    "TrafficPolicy",
]
