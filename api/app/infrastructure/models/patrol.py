"""SQLAlchemy records for Ops Patrol."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

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

from .base import Base

# Columns that update_from_domain must never overwrite from a domain snapshot.
# ``last_event_position`` is the formal projector's optimistic guard column
# (see PostgresFormalProjector); the domain models do not carry it, so copying a
# fresh from_domain() replacement would reset it to NULL and defeat the guard.
_PROJECTOR_GUARDED_COLUMNS = frozenset({"id", "last_event_position"})


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class PatrolPackModel(Base):
    __tablename__ = "patrol_packs"
    __table_args__ = (
        Index(
            "uq_patrol_packs_personal_slug",
            "owner_user_id",
            "slug",
            unique=True,
            postgresql_where=text("team_id IS NULL AND deleted_at IS NULL"),
        ),
        Index(
            "uq_patrol_packs_team_slug",
            "team_id",
            "slug",
            unique=True,
            postgresql_where=text("team_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index("ix_patrol_packs_scope_status", "team_id", "owner_user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,  # users RESTRICT FK integrity scan (composite scope index does not lead with owner_user_id)
    )
    team_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )  # indexed via ix_patrol_packs_scope_status (leading team_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Historical Patrol evidence retains the Integration ID even after the
    # Integration is removed. Runtime admission validates the current record;
    # a database FK would make soft-deleted Packs pin Integrations forever.
    mcp_server_id: Mapped[str] = mapped_column(String(255), nullable=False)
    scheduled_job_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("scheduled_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_validated_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    validation_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # 投影器乐观守卫：最近应用到本行执行态列的 execution_events.position。
    # 仅由 PostgresFormalProjector 写；update_from_domain 显式跳过该列以免被回退。
    last_event_position: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def to_domain(self) -> PatrolPack:
        return PatrolPack(
            id=self.id,
            owner_user_id=self.owner_user_id,
            team_id=self.team_id,
            name=self.name,
            slug=self.slug,
            status=PatrolPackStatus(self.status),
            version=self.version,
            config=PatrolPackConfig.model_validate(self.config),
            mcp_server_id=self.mcp_server_id,
            scheduled_job_id=self.scheduled_job_id,
            last_validated_at=_utc(self.last_validated_at),
            last_validated_version=self.last_validated_version,
            validation_run_id=self.validation_run_id,
            validation_summary=dict(self.validation_summary or {}),
            deleted_at=_utc(self.deleted_at),
            created_at=_utc(self.created_at),
            updated_at=_utc(self.updated_at),
        )

    @classmethod
    def from_domain(cls, pack: PatrolPack) -> PatrolPackModel:
        return cls(
            id=pack.id,
            owner_user_id=pack.owner_user_id,
            team_id=pack.team_id,
            name=pack.name,
            slug=pack.slug,
            status=pack.status.value,
            version=pack.version,
            config=pack.config.model_dump(mode="json"),
            mcp_server_id=pack.mcp_server_id,
            scheduled_job_id=pack.scheduled_job_id,
            last_validated_at=pack.last_validated_at,
            last_validated_version=pack.last_validated_version,
            validation_run_id=pack.validation_run_id,
            validation_summary=pack.validation_summary,
            deleted_at=pack.deleted_at,
            created_at=pack.created_at,
            updated_at=pack.updated_at,
        )

    def update_from_domain(self, pack: PatrolPack) -> None:
        replacement = self.from_domain(pack)
        for column in self.__table__.columns:
            # last_event_position 是投影器独占的乐观守卫列，不随领域写路径回退。
            if column.name not in _PROJECTOR_GUARDED_COLUMNS:
                setattr(self, column.name, getattr(replacement, column.name))


class PatrolRunModel(Base):
    __tablename__ = "patrol_runs"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_patrol_runs_session_id"),
        UniqueConstraint("idempotency_key", name="uq_patrol_runs_idempotency_key"),
        Index(
            "uq_patrol_runs_active_pack",
            "pack_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_patrol_runs_pack_created", "pack_id", "created_at"),
        Index("ix_patrol_runs_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pack_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("patrol_packs.id", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    execution_run_id: Mapped[Any] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    pack_version: Mapped[int] = mapped_column(Integer, nullable=False)
    pack_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    submission_idempotency_key: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=""
    )
    collector_capability_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default=""
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pass_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    warn_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    evidence_completeness: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    report_artifact_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 投影器乐观守卫：最近应用到本行执行态列的 execution_events.position。
    # 仅由 PostgresFormalProjector 写；update_from_domain 显式跳过该列以免被回退。
    last_event_position: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def to_domain(self) -> PatrolRun:
        return PatrolRun(
            id=self.id,
            pack_id=self.pack_id,
            session_id=self.session_id,
            execution_run_id=self.execution_run_id,
            pack_version=self.pack_version,
            pack_snapshot=dict(self.pack_snapshot or {}),
            trigger_type=PatrolTriggerType(self.trigger_type),
            status=PatrolRunStatus(self.status),
            idempotency_key=self.idempotency_key,
            submission_idempotency_key=self.submission_idempotency_key,
            collector_capability_hash=self.collector_capability_hash,
            started_at=_utc(self.started_at),
            finished_at=_utc(self.finished_at),
            first_reviewed_at=_utc(self.first_reviewed_at),
            duration_ms=self.duration_ms,
            pass_count=self.pass_count,
            warn_count=self.warn_count,
            fail_count=self.fail_count,
            error_count=self.error_count,
            skipped_count=self.skipped_count,
            evidence_completeness=float(self.evidence_completeness)
            if self.evidence_completeness is not None
            else None,
            summary=dict(self.summary or {}),
            report_artifact_id=self.report_artifact_id,
            created_at=_utc(self.created_at),
            updated_at=_utc(self.updated_at),
        )

    @classmethod
    def from_domain(cls, run: PatrolRun) -> PatrolRunModel:
        return cls(
            **{
                **run.model_dump(mode="python"),
                "trigger_type": run.trigger_type.value,
                "status": run.status.value,
            }
        )

    def update_from_domain(self, run: PatrolRun) -> None:
        replacement = self.from_domain(run)
        for column in self.__table__.columns:
            # last_event_position 是投影器独占的乐观守卫列，不随领域写路径回退。
            if column.name not in _PROJECTOR_GUARDED_COLUMNS:
                setattr(self, column.name, getattr(replacement, column.name))


class PatrolCheckResultModel(Base):
    __tablename__ = "patrol_check_results"
    __table_args__ = (
        UniqueConstraint("run_id", "check_id", name="uq_patrol_check_results_run_check"),
        Index("ix_patrol_check_results_run_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("patrol_runs.id", ondelete="CASCADE"), nullable=False
    )
    check_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    observed: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    assertion_results: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    evidence_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    explanation: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_domain(self) -> PatrolCheckResult:
        return PatrolCheckResult(
            id=self.id,
            run_id=self.run_id,
            check_id=self.check_id,
            status=PatrolCheckStatus(self.status),
            severity=PatrolFindingSeverity(self.severity),
            observed=dict(self.observed or {}),
            assertion_results=list(self.assertion_results or []),
            evidence_refs=list(self.evidence_refs or []),
            explanation=self.explanation,
            error_code=self.error_code,
            error_message=self.error_message,
            fingerprint=self.fingerprint,
            started_at=_utc(self.started_at),
            finished_at=_utc(self.finished_at),
        )

    @classmethod
    def from_domain(cls, item: PatrolCheckResult) -> PatrolCheckResultModel:
        return cls(
            **{
                **item.model_dump(mode="python"),
                "status": item.status.value,
                "severity": item.severity.value,
            }
        )


class PatrolFindingModel(Base):
    __tablename__ = "patrol_findings"
    __table_args__ = (
        UniqueConstraint("run_id", "fingerprint", name="uq_patrol_findings_run_fingerprint"),
        Index("ix_patrol_findings_fingerprint_status", "fingerprint", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("patrol_runs.id", ondelete="CASCADE"), nullable=False
    )
    check_result_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("patrol_check_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="open")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    decided_by: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_domain(self) -> PatrolFinding:
        return PatrolFinding(
            id=self.id,
            run_id=self.run_id,
            check_result_id=self.check_result_id,
            fingerprint=self.fingerprint,
            severity=PatrolFindingSeverity(self.severity),
            status=PatrolFindingStatus(self.status),
            title=self.title,
            summary=self.summary,
            first_seen_at=_utc(self.first_seen_at),
            last_seen_at=_utc(self.last_seen_at),
            occurrence_count=self.occurrence_count,
            decided_by=self.decided_by,
            decided_at=_utc(self.decided_at),
            decision_reason=self.decision_reason,
        )

    @classmethod
    def from_domain(cls, finding: PatrolFinding) -> PatrolFindingModel:
        return cls(
            **{
                **finding.model_dump(mode="python"),
                "severity": finding.severity.value,
                "status": finding.status.value,
            }
        )

    def update_from_domain(self, finding: PatrolFinding) -> None:
        replacement = self.from_domain(finding)
        for column in self.__table__.columns:
            if column.name != "id":
                setattr(self, column.name, getattr(replacement, column.name))


# Kept in sync with PATROL_REMEDIATION_TERMINAL_STATUSES in app.domain.models.patrol;
# used only for the raw-SQL partial-unique-index predicate below.
_NON_TERMINAL_REMEDIATION_STATUSES_SQL = "status NOT IN ('verified', 'failed', 'cancelled')"


class PatrolRemediationModel(Base):
    __tablename__ = "patrol_remediations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_patrol_remediations_idempotency_key"),
        Index(
            "uq_patrol_remediations_active_finding",
            "finding_id",
            unique=True,
            postgresql_where=text(_NON_TERMINAL_REMEDIATION_STATUSES_SQL),
        ),
        Index("ix_patrol_remediations_fingerprint", "fingerprint"),
        Index("ix_patrol_remediations_run_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pack_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("patrol_packs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,  # patrol_packs RESTRICT FK integrity scan
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("patrol_runs.id", ondelete="CASCADE"), nullable=False
    )  # indexed via ix_patrol_remediations_run_id
    finding_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("patrol_findings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,  # full FK index; uq_patrol_remediations_active_finding is partial (non-terminal only)
    )
    check_result_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("patrol_check_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    target_workload: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    target_kind: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Deployment"
    )
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    impact_summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    rollback_hint: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    actuator_capability_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="proposed")
    before_observation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_observation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    recheck_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("patrol_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 投影器乐观守卫：最近应用到本行执行态列的 execution_events.position。
    # 仅由 PostgresFormalProjector 写；update_from_domain 显式跳过该列以免被回退。
    last_event_position: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,  # users RESTRICT FK integrity scan
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def to_domain(self) -> PatrolRemediation:
        return PatrolRemediation(
            id=self.id,
            pack_id=self.pack_id,
            run_id=self.run_id,
            finding_id=self.finding_id,
            check_result_id=self.check_result_id,
            fingerprint=self.fingerprint,
            session_id=self.session_id,
            action=PatrolRemediationAction(self.action),
            target_namespace=self.target_namespace,
            target_workload=self.target_workload,
            target_kind=self.target_kind,
            params=dict(self.params or {}),
            params_hash=self.params_hash,
            impact_summary=self.impact_summary,
            rollback_hint=self.rollback_hint,
            idempotency_key=self.idempotency_key,
            actuator_capability_hash=self.actuator_capability_hash,
            status=PatrolRemediationStatus(self.status),
            before_observation=dict(self.before_observation)
            if self.before_observation is not None
            else None,
            after_observation=dict(self.after_observation)
            if self.after_observation is not None
            else None,
            recheck_run_id=self.recheck_run_id,
            error_code=self.error_code,
            error_message=self.error_message,
            created_by=self.created_by,
            created_at=_utc(self.created_at),
            updated_at=_utc(self.updated_at),
        )

    @classmethod
    def from_domain(cls, remediation: PatrolRemediation) -> PatrolRemediationModel:
        return cls(
            **{
                **remediation.model_dump(mode="python"),
                "action": remediation.action.value,
                "status": remediation.status.value,
            }
        )

    def update_from_domain(self, remediation: PatrolRemediation) -> None:
        replacement = self.from_domain(remediation)
        for column in self.__table__.columns:
            # last_event_position 是投影器独占的乐观守卫列，不随领域写路径回退。
            if column.name not in _PROJECTOR_GUARDED_COLUMNS:
                setattr(self, column.name, getattr(replacement, column.name))
