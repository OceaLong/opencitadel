"""SQLAlchemy persistence models for the execution-kernel foundation."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.models.base import Base


def _owner_scope_constraint(table: str) -> CheckConstraint:
    return CheckConstraint(
        "(owner_user_id IS NOT NULL AND team_id IS NULL) OR "
        "(owner_user_id IS NULL AND team_id IS NOT NULL)",
        name=f"ck_{table}_owner_scope",
    )


_OWNER_SCOPE_KEY_SQL = (
    "CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END"
)


class ExecutionStreamOwnerORM(Base):
    """Database-enforced immutable OwnerScope for one execution stream."""

    __tablename__ = "execution_stream_owners"
    __table_args__ = (
        UniqueConstraint(
            "stream_type",
            "stream_id",
            "owner_scope_key",
            name="uq_execution_stream_owners_scope",
        ),
        _owner_scope_constraint("execution_stream_owners"),
    )

    stream_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    stream_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_scope_key: Mapped[str] = mapped_column(
        String(261),
        Computed(_OWNER_SCOPE_KEY_SQL, persisted=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ExecutionEventORM(Base):
    __tablename__ = "execution_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_execution_events_event_id"),
        UniqueConstraint(
            "stream_type",
            "stream_id",
            "stream_version",
            name="uq_execution_events_stream_version",
        ),
        ForeignKeyConstraint(
            ("stream_type", "stream_id", "owner_scope_key"),
            (
                "execution_stream_owners.stream_type",
                "execution_stream_owners.stream_id",
                "execution_stream_owners.owner_scope_key",
            ),
            name="fk_execution_events_stream_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint("stream_version > 0", name="ck_execution_events_stream_version"),
        CheckConstraint(
            "event_schema_version > 0",
            name="ck_execution_events_schema_version",
        ),
        _owner_scope_constraint("execution_events"),
        Index(
            "ix_execution_events_stream",
            "stream_type",
            "stream_id",
            "stream_version",
        ),
        Index(
            "ix_execution_events_owner_position",
            "owner_user_id",
            "position",
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
        Index(
            "ix_execution_events_team_position",
            "team_id",
            "position",
            postgresql_where=text("team_id IS NOT NULL"),
        ),
        Index("ix_execution_events_type_position", "event_type", "position"),
    )

    position: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    stream_type: Mapped[str] = mapped_column(String(64), nullable=False)
    stream_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stream_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    public_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    internal_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    secret_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_scope_key: Mapped[str] = mapped_column(
        String(261),
        Computed(_OWNER_SCOPE_KEY_SQL, persisted=True),
        nullable=False,
    )
    correlation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    causation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ExecutionCommandInboxORM(Base):
    __tablename__ = "execution_command_inbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'processing', 'accepted', 'rejected', 'dead_lettered')",
            name="ck_execution_command_inbox_status",
        ),
        CheckConstraint(
            "expected_stream_version IS NULL OR expected_stream_version >= 0",
            name="ck_execution_command_inbox_expected_version",
        ),
        _owner_scope_constraint("execution_command_inbox"),
        Index(
            "ix_execution_command_inbox_claim",
            "status",
            "claim_deadline",
            "received_at",
        ),
        Index(
            "ix_execution_command_inbox_stream",
            "stream_type",
            "stream_id",
            "received_at",
        ),
    )

    command_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    command_type: Mapped[str] = mapped_column(String(128), nullable=False)
    command_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    stream_type: Mapped[str] = mapped_column(String(64), nullable=False)
    stream_id: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_stream_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    correlation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    causation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    payload_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="received")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Real processing deliveries (PostgresInbox.claim only). claim_generation
    # also climbs on the kernel's batch pre-claim (PostgresInboxSource), so it
    # fences leases but over-counts deliveries; the dead-letter cap uses this.
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    claim_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_event_position: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_event_position: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rejection_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ExecutionOutboxORM(Base):
    __tablename__ = "execution_outbox"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_execution_outbox_dedupe_key"),
        _owner_scope_constraint("execution_outbox"),
        Index(
            "ix_execution_outbox_delivery",
            "delivered_at",
            "available_at",
            "claim_deadline",
        ),
    )

    outbox_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    event_position: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "execution_events.position",
            name="fk_execution_outbox_event_position",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,  # execution_events RESTRICT FK integrity scan
    )
    destination: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # Optional destination-specific message body (K4-2). Wakeup hints carry no
    # payload (NULL); durable approval notices persist the notice data here so
    # the dispatcher can redeliver after a crash without re-reading projections.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    claim_generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    claim_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ExecutionScheduledCommandORM(Base):
    __tablename__ = "execution_scheduled_commands"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'fired', 'cancelled', 'dead_lettered')",
            name="ck_execution_scheduled_commands_status",
        ),
        _owner_scope_constraint("execution_scheduled_commands"),
        Index(
            "ix_execution_scheduled_commands_due",
            "status",
            "due_at",
            "claim_deadline",
        ),
    )

    timer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    command_envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    cancellation_event_types: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    cancellation_activity_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    cancellation_event_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    claimed_generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    claim_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionActivityTaskORM(Base):
    __tablename__ = "execution_activity_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'claimed', 'call_started', 'succeeded', "
            "'failed', 'unknown', 'cancelled', 'dead_lettered')",
            name="ck_execution_activity_tasks_status",
        ),
        _owner_scope_constraint("execution_activity_tasks"),
        Index(
            "ix_execution_activity_tasks_claim",
            "status",
            "available_at",
            "claim_deadline",
        ),
        Index(
            "ix_execution_activity_tasks_aggregate",
            "aggregate_type",
            "aggregate_id",
            "created_at",
        ),
    )

    activity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(255), nullable=False)
    activity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    request_event_position: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "execution_events.position",
            name="fk_execution_activity_request_event",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,  # execution_events RESTRICT FK integrity scan
    )
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    request_generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    claim_generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Poison-pill guard: total number of worker claims (including lease-expiry
    # reclaims). ``attempt`` only counts call_started transitions, so a task
    # that crashes the worker *before* call start would otherwise be reclaimed
    # forever. Once claim_attempts exceeds the configured cap the store parks
    # the row as ``dead_lettered`` instead of claiming it again.
    claim_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    claimed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claim_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    call_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeout_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    request_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    result_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full decision payload of a succeeded activity (e.g. model tool_calls),
    # written in the settlement transaction. The ActivityCompleted event keeps
    # only a digest of this value; the decision source rehydrates and verifies
    # it before planning.
    decision_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
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


class ExecutionPoisonedRunORM(Base):
    """Kernel-internal quarantine for Run projections that fail to decode.

    A single corrupt projection row (state-hash mismatch, stale policy metadata,
    or invalid state) must never abort the whole decision batch or crash every
    kernel replica in a CrashLoopBackOff. The decision source records the
    offending Run here, skips it, and excludes it from future ``load_ready``
    scans so healthy Runs keep progressing. This is an operator diagnostic /
    control table (no tenant RLS), written only by the execution kernel.
    """

    __tablename__ = "execution_poisoned_runs"
    __table_args__ = (Index("ix_execution_poisoned_runs_last_seen", "last_seen_at"),)

    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    last_error: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ExecutionPoisonedScopeORM(Base):
    """Kernel-internal quarantine for owner scopes whose projection keeps failing.

    One corrupt projection row (or a scope-local bug) must never stall the whole
    formal-projection loop: after N consecutive per-scope failures the kernel
    records the scope here and ``list_pending`` stops offering it, so every
    other scope keeps projecting (D12/P1-13). The row doubles as the rebuild
    marker: ``rebuilding = true`` while an operator-driven projection rebuild is
    in flight, letting the run-context source hand out a retryable signal
    instead of a permanent policy failure. Operator diagnostic / control table
    (no tenant RLS), mirroring ``execution_poisoned_runs``.
    """

    __tablename__ = "execution_poisoned_scopes"
    __table_args__ = (Index("ix_execution_poisoned_scopes_last_seen", "last_seen_at"),)

    owner_scope_key: Mapped[str] = mapped_column(String(261), primary_key=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    last_error: Mapped[str] = mapped_column(Text, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rebuilding: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ExecutionScopeHeadORM(Base):
    """Per owner-scope high-water mark of appended event positions.

    The formal projector's owner-scope discovery must never re-aggregate the
    ever-growing ``execution_events`` table on every idle poll. The append path
    keeps this compact head row (one per owner scope) current inside the same
    transaction that writes the events, so ``list_pending`` degrades to an
    indexed ``head_position > checkpoint`` lookup that is flat in the event
    count. Kernel-internal control table (no tenant RLS, mirroring
    ``execution_poisoned_runs``); it carries no event payload, only a
    monotonically advancing position marker keyed by the owner-scope key.
    """

    __tablename__ = "execution_scope_head"

    owner_scope_key: Mapped[str] = mapped_column(String(261), primary_key=True)
    head_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ExecutionSnapshotORM(Base):
    __tablename__ = "execution_snapshots"
    __table_args__ = (
        CheckConstraint("stream_version >= 0", name="ck_execution_snapshots_stream_version"),
        CheckConstraint(
            "serializer_version > 0",
            name="ck_execution_snapshots_serializer_version",
        ),
        _owner_scope_constraint("execution_snapshots"),
        Index(
            "ix_execution_snapshots_latest",
            "stream_type",
            "stream_id",
            text("stream_version DESC"),
        ),
    )

    stream_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    stream_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    stream_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    serializer_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ExecutionProjectorCheckpointORM(Base):
    __tablename__ = "execution_projector_checkpoints"
    __table_args__ = (
        CheckConstraint(
            "last_position >= 0",
            name="ck_execution_projector_checkpoints_position",
        ),
        _owner_scope_constraint("execution_projector_checkpoints"),
    )

    projector_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_scope_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_position: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ExecutionRunProjectionORM(Base):
    """Rebuildable query projection for the universal Run aggregate."""

    __tablename__ = "execution_run_projection"
    __table_args__ = (
        _owner_scope_constraint("execution_run_projection"),
        CheckConstraint("stream_version > 0", name="ck_execution_run_projection_version"),
        Index(
            "ix_execution_run_projection_owner_status",
            "owner_user_id",
            "status",
            "updated_at",
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
        Index(
            "ix_execution_run_projection_team_status",
            "team_id",
            "status",
            "updated_at",
            postgresql_where=text("team_id IS NOT NULL"),
        ),
        Index(
            "ix_execution_run_projection_source",
            "source_entity_type",
            "source_entity_id",
        ),
        # Decision-readiness scan: load_ready reads only armed rows in due order.
        Index(
            "ix_execution_run_projection_decision_due",
            "decision_due_at",
            "run_id",
            postgresql_where=text("decision_due_at IS NOT NULL AND terminal = false"),
        ),
    )

    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    source_entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    execution_policy_revision_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    execution_policy_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Decision-readiness model (D4 / P0-1): the projector maintains these three
    # columns from the evolved RunState so ``load_ready`` filters decidable Runs
    # in SQL instead of decoding every non-terminal projection row per poll.
    # ``decision_due_at`` is armed whenever the projection says the Run needs a
    # decision (queued, or running with no active activities) and cleared by the
    # decision worker once a decision round found nothing to do (disarm).
    wait_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    active_activity_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    decision_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parent_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    correlation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stream_version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_event_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionResourceBuildProjectionORM(Base):
    """Rebuildable product projection for knowledge-base build runs."""

    __tablename__ = "execution_resource_build_projection"
    __table_args__ = (
        _owner_scope_constraint("execution_resource_build_projection"),
        CheckConstraint(
            "stream_version > 0",
            name="ck_execution_resource_build_projection_version",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_execution_resource_build_projection_progress",
        ),
        Index(
            "ix_execution_resource_build_projection_resource",
            "resource_kind",
            "resource_id",
            "updated_at",
        ),
        Index(
            "ix_execution_resource_build_projection_owner_status",
            "owner_user_id",
            "status",
            "updated_at",
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
        Index(
            "ix_execution_resource_build_projection_team_status",
            "team_id",
            "status",
            "updated_at",
            postgresql_where=text("team_id IS NOT NULL"),
        ),
    )

    build_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    resource_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    phase: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    candidate_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stream_version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_event_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionPublicEventORM(Base):
    """Sanitized durable event feed for the user-facing SSE/history stream.

    Rows come from two producers: the formal projector (shaped from
    ``execution_events``, carrying the source event's global ``position``) and
    the activity worker's progress sink (ephemeral telemetry that never enters
    the hash-chained aggregate stream, ``position`` is NULL). The feed's total
    order — and the public cursor — is the table's own ``seq``.
    """

    __tablename__ = "execution_public_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_execution_public_events_event_id"),
        _owner_scope_constraint("execution_public_events"),
        Index(
            "ix_execution_public_events_stream",
            "stream_type",
            "stream_id",
            "seq",
        ),
        Index(
            "ix_execution_public_events_source",
            "source_entity_type",
            "source_entity_id",
            "seq",
        ),
        Index(
            "ix_execution_public_events_owner_seq",
            "owner_user_id",
            "seq",
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
        Index(
            "ix_execution_public_events_team_seq",
            "team_id",
            "seq",
            postgresql_where=text("team_id IS NOT NULL"),
        ),
    )

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Source position in execution_events for projector-derived rows; NULL for
    # off-stream telemetry rows (activity progress).
    position: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    source_entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_entity_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stream_type: Mapped[str] = mapped_column(String(64), nullable=False)
    stream_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # 0 for off-stream telemetry rows, which have no aggregate stream version.
    stream_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExecutionActivityProjectionORM(Base):
    """Rebuildable operator-facing projection of durable activities."""

    __tablename__ = "execution_activity_projection"
    __table_args__ = (
        _owner_scope_constraint("execution_activity_projection"),
        CheckConstraint(
            "stream_version > 0",
            name="ck_execution_activity_projection_version",
        ),
        Index(
            "ix_execution_activity_projection_run",
            "run_id",
            "updated_at",
        ),
        Index(
            "ix_execution_activity_projection_owner_status",
            "owner_user_id",
            "status",
            "updated_at",
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
        Index(
            "ix_execution_activity_projection_team_status",
            "team_id",
            "status",
            "updated_at",
            postgresql_where=text("team_id IS NOT NULL"),
        ),
    )

    activity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    activity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stream_version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_event_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionApprovalProjectionORM(Base):
    """Rebuildable approval lifecycle projected from Run events."""

    __tablename__ = "execution_approval_projection"
    __table_args__ = (
        _owner_scope_constraint("execution_approval_projection"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired')",
            name="ck_execution_approval_projection_status",
        ),
        Index(
            "ix_execution_approval_projection_run",
            "run_id",
            "requested_at",
        ),
        Index(
            "ix_execution_approval_projection_owner_status",
            "owner_user_id",
            "status",
            "requested_at",
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
        Index(
            "ix_execution_approval_projection_team_status",
            "team_id",
            "status",
            "requested_at",
            postgresql_where=text("team_id IS NOT NULL"),
        ),
    )

    approval_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    approval_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_activity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    subject_label: Mapped[str] = mapped_column(String(255), nullable=False)
    risk_summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    feedback: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_event_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    decision_event_position: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "ExecutionActivityProjectionORM",
    "ExecutionActivityTaskORM",
    "ExecutionApprovalProjectionORM",
    "ExecutionCommandInboxORM",
    "ExecutionEventORM",
    "ExecutionOutboxORM",
    "ExecutionPoisonedRunORM",
    "ExecutionPoisonedScopeORM",
    "ExecutionProjectorCheckpointORM",
    "ExecutionPublicEventORM",
    "ExecutionResourceBuildProjectionORM",
    "ExecutionRunProjectionORM",
    "ExecutionScheduledCommandORM",
    "ExecutionScopeHeadORM",
    "ExecutionSnapshotORM",
    "ExecutionStreamOwnerORM",
]
