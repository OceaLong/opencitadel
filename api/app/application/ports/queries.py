"""Persistence-agnostic read models and query capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from app.application.execution.public_projection import PublicEventPage
from app.domain.execution.run import RunStatus
from app.domain.models.audit_log import AuditLog
from app.domain.models.scope import OwnerScope

UsageBreakdownDimension = Literal["model", "user", "team", "agent"]


@dataclass(frozen=True)
class AuditCountPoint:
    key: str
    count: int


@dataclass(frozen=True)
class AuditSummary:
    by_day: tuple[AuditCountPoint, ...]
    by_action: tuple[AuditCountPoint, ...]


@dataclass(frozen=True)
class ComplianceEvidenceSnapshot:
    audit_count: int
    operator_scope_count: int
    operator_sessions: int
    auth_event_count: int
    role_distribution: dict[str, int]
    inference_endpoint_hosts: tuple[str, ...]
    evidence_export_count: int
    admin_action_count: int
    redaction_sample_logs: tuple[AuditLog, ...]
    timestamp_chain_logs: tuple[AuditLog, ...]


@dataclass(frozen=True)
class EvidenceSession:
    session_id: str
    title: str
    owner_user_id: str | None
    team_id: str | None
    operator_scope: str | None
    status: str
    updated_at: datetime | None


@dataclass(frozen=True)
class QuotaUsageSnapshot:
    daily_sessions: int
    monthly_tokens: int
    storage_bytes: int


@dataclass(frozen=True)
class UsageSummary:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int
    call_count: int


@dataclass(frozen=True)
class UsageTimePoint:
    date: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int
    call_count: int


@dataclass(frozen=True)
class UsageBreakdownRow:
    key: str
    total_tokens: int
    call_count: int


@dataclass(frozen=True)
class PatrolRetentionResult:
    runs_deleted: int
    findings_deleted: int
    evidence_refs_purged: int


@dataclass(frozen=True)
class ResourceBuildView:
    build_id: str
    run_id: UUID
    resource_kind: str
    resource_id: str
    status: RunStatus
    phase: str | None
    progress: int
    active_version_id: str | None
    candidate_version_id: str
    failure_code: str | None
    created_at: datetime
    updated_at: datetime
    terminal_at: datetime | None


@runtime_checkable
class AuditSummaryQueryPort(Protocol):
    async def summarize(
        self,
        *,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> AuditSummary: ...


@runtime_checkable
class ComplianceEvidenceQueryPort(Protocol):
    async def collect(
        self,
        *,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> ComplianceEvidenceSnapshot: ...


@runtime_checkable
class EvidenceSessionQueryPort(Protocol):
    async def list_sessions(self, *, limit: int, offset: int) -> tuple[EvidenceSession, ...]: ...


@runtime_checkable
class QuotaUsageQueryPort(Protocol):
    async def snapshot(
        self,
        *,
        user_id: str,
        session_since: datetime,
        token_since: datetime,
    ) -> QuotaUsageSnapshot: ...


@runtime_checkable
class UsageQueryPort(Protocol):
    async def aggregate(
        self,
        *,
        owner_user_id: str | None,
        team_id: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> UsageSummary: ...

    async def timeseries(
        self,
        *,
        owner_user_id: str | None,
        team_id: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> tuple[UsageTimePoint, ...]: ...

    async def breakdown(
        self,
        *,
        dimension: UsageBreakdownDimension,
        owner_user_id: str | None,
        team_id: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
        limit: int,
    ) -> tuple[UsageBreakdownRow, ...]: ...


@runtime_checkable
class PatrolRetentionStorePort(Protocol):
    async def cleanup(
        self,
        *,
        run_cutoff: datetime,
        finding_cutoff: datetime,
        evidence_cutoff: datetime,
        limit: int,
    ) -> PatrolRetentionResult: ...


@runtime_checkable
class RunProjectionPort(Protocol):
    async def latest_active_run_id(
        self,
        *,
        source_entity_type: str,
        source_entity_id: str,
        owner_scope: OwnerScope,
    ) -> UUID | None: ...

    async def run_id_for_pending_approval(
        self,
        *,
        approval_id: UUID,
        owner_scope: OwnerScope,
    ) -> UUID | None: ...

    async def status_for_run(
        self,
        *,
        run_id: UUID,
        owner_scope: OwnerScope,
    ) -> RunStatus | None: ...

    async def approval_stats(self, since: datetime) -> dict[str, Any]: ...

    async def execution_metrics(
        self,
        *,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> dict[str, int]: ...

    async def governance_daily(self, since: datetime) -> list[dict[str, Any]]: ...

    async def source_governance(
        self,
        *,
        source_entity_type: str,
        source_entity_id: str,
        owner_scope: OwnerScope,
    ) -> dict[str, Any]: ...

    async def resource_build(
        self,
        *,
        build_id: str,
        owner_scope: OwnerScope,
    ) -> ResourceBuildView | None: ...


@runtime_checkable
class PublicProjectionPort(Protocol):
    async def list_events(
        self,
        *,
        source_entity_type: str,
        source_entity_id: str,
        owner_scope: OwnerScope,
        run_id: UUID | None = None,
        after: str | None = None,
        before: str | None = None,
        latest: bool = False,
        limit: int = 100,
    ) -> PublicEventPage: ...
