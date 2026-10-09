"""Rebuildable read models and durable provenance/usage/observation records.

Writers serialize observation order by locking the run row and incrementing its
observed_order in the same transaction as journal and projection writes. Never
allocate order from a sequence or infer order from timestamps.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from app.infrastructure.models.base import Base

metadata = Base.metadata

execution_view_runs = sa.Table(
    "execution_view_runs",
    metadata,
    sa.Column("run_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("family", sa.String(255), nullable=False),
    sa.Column("status", sa.String(255), nullable=False),
    sa.Column("wait_reason", sa.Text(), nullable=True),
    sa.Column("source", JSONB(), nullable=True),
    sa.Column("purpose", sa.String(255), nullable=False),
    sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("configuration_revision", sa.String(255), nullable=True),
    sa.Column("model_revision", sa.String(255), nullable=True),
    sa.Column("execution_mode", sa.String(255), nullable=True),
    sa.Column("configuration", JSONB(), nullable=True),
    sa.Column("public_summary", sa.Text(), nullable=True),
    sa.Column("completeness", JSONB(), nullable=False),
    sa.Column("capabilities", JSONB(), nullable=False),
    sa.Column("projection_revision", sa.BigInteger(), nullable=False),
    sa.Column("projector_version", sa.Integer(), nullable=False),
    sa.Column("as_of", sa.DateTime(timezone=True), nullable=True),
    sa.Column("latest_available", sa.DateTime(timezone=True), nullable=True),
    sa.Column("formal_position", sa.BigInteger(), nullable=False),
    sa.Column("progress_position", sa.BigInteger(), nullable=False),
    sa.Column("observed_order", sa.BigInteger(), nullable=False),
    sa.Column("owner_user_id", sa.String(255), nullable=True),
    sa.Column("team_id", sa.String(255), nullable=True),
    sa.Column(
        "scope_key",
        sa.String(261),
        sa.Computed(
            "CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END",
            persisted=True,
        ),
        nullable=False,
    ),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.UniqueConstraint("run_id", "scope_key", name="uq_execution_view_runs_scope"),
    sa.CheckConstraint(
        "(owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)",
        name="ck_execution_view_runs_owner_scope",
    ),
    sa.CheckConstraint("schema_version > 0", name="ck_execution_view_runs_schema_version"),
    sa.Index("ix_execution_view_runs_scope_time", "scope_key", "created_at"),
    sa.CheckConstraint(
        "projection_revision >= 0", name="ck_execution_view_runs_projection_revision"
    ),
    sa.CheckConstraint("formal_position >= 0", name="ck_execution_view_runs_formal_position"),
    sa.CheckConstraint("progress_position >= 0", name="ck_execution_view_runs_progress_position"),
    sa.CheckConstraint("observed_order >= 0", name="ck_execution_view_runs_observed_order"),
    sa.Index("ix_execution_view_runs_admitted", "scope_key", "admitted_at", "run_id"),
    sa.Index("ix_execution_view_runs_status", "scope_key", "status", "admitted_at"),
    sa.Index(
        "ix_execution_view_runs_configuration",
        "scope_key",
        "configuration_revision",
        "execution_mode",
        "admitted_at",
    ),
)

execution_view_steps = sa.Table(
    "execution_view_steps",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("run_id", sa.UUID(), nullable=False),
    sa.Column("step_id", sa.String(255), nullable=False),
    sa.Column("activity_id", sa.UUID(), nullable=True),
    sa.Column("invocation_id", sa.UUID(), nullable=True),
    sa.Column("attempt_id", sa.String(255), nullable=True),
    sa.Column("logical_step_id", sa.String(255), nullable=True),
    sa.Column("parent_step_id", sa.String(255), nullable=True),
    sa.Column("semantic_key", sa.String(255), nullable=True),
    sa.Column("relationship", sa.String(255), nullable=False),
    sa.Column("kind", sa.String(255), nullable=False),
    sa.Column("status", sa.String(255), nullable=False),
    sa.Column("wait_reason", sa.Text(), nullable=True),
    sa.Column("end_reason", sa.Text(), nullable=True),
    sa.Column("business_outcome", sa.String(255), nullable=True),
    sa.Column("tool_name", sa.String(255), nullable=True),
    sa.Column("tool_contract_revision", sa.String(255), nullable=True),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("duration_ms", sa.BigInteger(), nullable=True),
    sa.Column("first_persisted_output_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("public_summary", sa.Text(), nullable=True),
    sa.Column("input_ref", JSONB(), nullable=True),
    sa.Column("output_ref", JSONB(), nullable=True),
    sa.Column("artifact_refs", JSONB(), nullable=True),
    sa.Column("citation_refs", JSONB(), nullable=True),
    sa.Column("configuration", JSONB(), nullable=True),
    sa.Column("completeness", JSONB(), nullable=False),
    sa.Column("projection_revision", sa.BigInteger(), nullable=False),
    sa.Column("observed_order", sa.BigInteger(), nullable=False),
    sa.Column("owner_user_id", sa.String(255), nullable=True),
    sa.Column("team_id", sa.String(255), nullable=True),
    sa.Column(
        "scope_key",
        sa.String(261),
        sa.Computed(
            "CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END",
            persisted=True,
        ),
        nullable=False,
    ),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.CheckConstraint(
        "(owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)",
        name="ck_execution_view_steps_owner_scope",
    ),
    sa.CheckConstraint("schema_version > 0", name="ck_execution_view_steps_schema_version"),
    sa.Index("ix_execution_view_steps_scope_time", "scope_key", "created_at"),
    sa.ForeignKeyConstraint(
        ["run_id", "scope_key"],
        ["execution_view_runs.run_id", "execution_view_runs.scope_key"],
        name="fk_execution_view_steps_run_scope",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint("duration_ms >= 0", name="ck_execution_view_steps_duration_ms"),
    sa.CheckConstraint(
        "projection_revision >= 0", name="ck_execution_view_steps_projection_revision"
    ),
    sa.CheckConstraint("observed_order >= 0", name="ck_execution_view_steps_observed_order"),
    sa.UniqueConstraint("run_id", "step_id", name="uq_execution_view_steps_step"),
    sa.UniqueConstraint("run_id", "step_id", "attempt_id", name="uq_execution_view_steps_attempt"),
    sa.Index(
        "ix_execution_view_steps_parent", "run_id", "parent_step_id", "started_at", "attempt_id"
    ),
    sa.Index("ix_execution_view_steps_order", "run_id", "observed_order", "step_id"),
)

execution_view_checkpoints = sa.Table(
    "execution_view_checkpoints",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("run_id", sa.UUID(), nullable=False),
    sa.Column("boundary", sa.BigInteger(), nullable=False),
    sa.Column("projector_version", sa.Integer(), nullable=False),
    sa.Column("formal_position", sa.BigInteger(), nullable=False),
    sa.Column("progress_position", sa.BigInteger(), nullable=False),
    sa.Column("observed_order", sa.BigInteger(), nullable=False),
    sa.Column("projection_revision", sa.BigInteger(), nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("state_ref", JSONB(), nullable=False),
    sa.Column("owner_user_id", sa.String(255), nullable=True),
    sa.Column("team_id", sa.String(255), nullable=True),
    sa.Column(
        "scope_key",
        sa.String(261),
        sa.Computed(
            "CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END",
            persisted=True,
        ),
        nullable=False,
    ),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.CheckConstraint(
        "(owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)",
        name="ck_execution_view_checkpoints_owner_scope",
    ),
    sa.CheckConstraint("schema_version > 0", name="ck_execution_view_checkpoints_schema_version"),
    sa.Index("ix_execution_view_checkpoints_scope_time", "scope_key", "created_at"),
    sa.ForeignKeyConstraint(
        ["run_id", "scope_key"],
        ["execution_view_runs.run_id", "execution_view_runs.scope_key"],
        name="fk_execution_view_checkpoints_run_scope",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint("boundary >= 0", name="ck_execution_view_checkpoints_boundary"),
    sa.CheckConstraint(
        "formal_position >= 0", name="ck_execution_view_checkpoints_formal_position"
    ),
    sa.CheckConstraint(
        "progress_position >= 0", name="ck_execution_view_checkpoints_progress_position"
    ),
    sa.CheckConstraint("observed_order >= 0", name="ck_execution_view_checkpoints_observed_order"),
    sa.CheckConstraint(
        "projection_revision >= 0", name="ck_execution_view_checkpoints_projection_revision"
    ),
    sa.UniqueConstraint(
        "run_id", "boundary", "projector_version", name="uq_execution_view_checkpoints_boundary"
    ),
    sa.Index(
        "ix_execution_view_checkpoints_order", "run_id", "observed_order", "projector_version"
    ),
)

execution_view_observations = sa.Table(
    "execution_view_observations",
    metadata,
    sa.Column("run_id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("observed_order", sa.BigInteger(), primary_key=True, nullable=False),
    sa.Column("source_kind", sa.String(255), nullable=False),
    sa.Column("source_identity", sa.String(255), nullable=False),
    sa.Column("event_id", sa.UUID(), nullable=True),
    sa.Column("formal_position", sa.BigInteger(), nullable=False),
    sa.Column("progress_position", sa.BigInteger(), nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("projection_revision", sa.BigInteger(), nullable=False),
    sa.Column("projector_version", sa.Integer(), nullable=False),
    sa.Column("public_payload", JSONB(), nullable=False),
    sa.Column("owner_user_id", sa.String(255), nullable=True),
    sa.Column("team_id", sa.String(255), nullable=True),
    sa.Column(
        "scope_key",
        sa.String(261),
        sa.Computed(
            "CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END",
            persisted=True,
        ),
        nullable=False,
    ),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.CheckConstraint(
        "(owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)",
        name="ck_execution_view_observations_owner_scope",
    ),
    sa.CheckConstraint("schema_version > 0", name="ck_execution_view_observations_schema_version"),
    sa.Index("ix_execution_view_observations_scope_time", "scope_key", "created_at"),
    sa.ForeignKeyConstraint(
        ["run_id", "scope_key"],
        ["execution_view_runs.run_id", "execution_view_runs.scope_key"],
        name="fk_execution_view_observations_run_scope",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint("observed_order >= 0", name="ck_execution_view_observations_observed_order"),
    sa.CheckConstraint(
        "formal_position >= 0", name="ck_execution_view_observations_formal_position"
    ),
    sa.CheckConstraint(
        "progress_position >= 0", name="ck_execution_view_observations_progress_position"
    ),
    sa.CheckConstraint(
        "projection_revision >= 0", name="ck_execution_view_observations_projection_revision"
    ),
    sa.UniqueConstraint(
        "run_id",
        "source_kind",
        "source_identity",
        "projector_version",
        name="uq_execution_view_observations_source",
    ),
    sa.Index("ix_execution_view_observations_time", "run_id", "observed_at", "observed_order"),
)

artifact_version_provenance = sa.Table(
    "artifact_version_provenance",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("artifact_id", sa.String(255), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("producer_identity", sa.String(255), nullable=False),
    sa.Column("producer_run_id", sa.UUID(), nullable=True),
    sa.Column("producer_step_ids", JSONB(), nullable=True),
    sa.Column("activity_id", sa.UUID(), nullable=True),
    sa.Column("attempt_id", sa.String(255), nullable=True),
    sa.Column("invocation_id", sa.UUID(), nullable=True),
    sa.Column("produced_event_id", sa.UUID(), nullable=True),
    sa.Column("evidence_kind", sa.String(255), nullable=False),
    sa.Column("evidence", JSONB(), nullable=True),
    sa.Column("binding_status", sa.String(255), nullable=False),
    sa.Column("boundary", sa.BigInteger(), nullable=True),
    sa.Column("content_ref", JSONB(), nullable=True),
    sa.Column("content_digest", sa.String(255), nullable=True),
    sa.Column("citation_refs", JSONB(), nullable=True),
    sa.Column("availability", sa.String(255), nullable=False),
    sa.Column("revision", sa.BigInteger(), nullable=False),
    sa.Column("owner_user_id", sa.String(255), nullable=True),
    sa.Column("team_id", sa.String(255), nullable=True),
    sa.Column(
        "scope_key",
        sa.String(261),
        sa.Computed(
            "CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END",
            persisted=True,
        ),
        nullable=False,
    ),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.CheckConstraint(
        "(owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)",
        name="ck_artifact_version_provenance_owner_scope",
    ),
    sa.CheckConstraint("schema_version > 0", name="ck_artifact_version_provenance_schema_version"),
    sa.Index("ix_artifact_version_provenance_scope_time", "scope_key", "created_at"),
    sa.ForeignKeyConstraint(
        ["producer_run_id", "scope_key"],
        ["execution_view_runs.run_id", "execution_view_runs.scope_key"],
        name="fk_artifact_version_provenance_run_scope",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint("boundary >= 0", name="ck_artifact_version_provenance_boundary"),
    sa.CheckConstraint("revision >= 0", name="ck_artifact_version_provenance_revision"),
    sa.UniqueConstraint(
        "artifact_id",
        "version",
        "producer_identity",
        name="uq_artifact_version_provenance_producer",
    ),
    sa.Index("ix_artifact_version_provenance_run_boundary", "producer_run_id", "boundary"),
    sa.CheckConstraint("version > 0", name="ck_artifact_version_provenance_version"),
    sa.CheckConstraint(
        "binding_status IN ('pending', 'bound', 'unavailable')",
        name="ck_artifact_version_provenance_binding",
    ),
    sa.CheckConstraint(
        "binding_status <> 'bound' OR producer_run_id IS NOT NULL",
        name="ck_artifact_version_provenance_bound_run",
    ),
    sa.CheckConstraint(
        "evidence_kind IN ('direct', 'derived', 'unknown')",
        name="ck_artifact_version_provenance_evidence_kind",
    ),
)

execution_usage_facts = sa.Table(
    "execution_usage_facts",
    metadata,
    sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
    sa.Column("call_identity", sa.String(255), nullable=False),
    sa.Column("run_id", sa.UUID(), nullable=False),
    sa.Column("activity_id", sa.UUID(), nullable=True),
    sa.Column("attempt_id", sa.String(255), nullable=True),
    sa.Column("purpose", sa.String(255), nullable=False),
    sa.Column("model_revision", sa.String(255), nullable=True),
    sa.Column("input_tokens", sa.BigInteger(), nullable=True),
    sa.Column("output_tokens", sa.BigInteger(), nullable=True),
    sa.Column("price_revision", sa.String(255), nullable=True),
    sa.Column("cost_usd", sa.Numeric(24, 12), nullable=True),
    sa.Column("coverage", JSONB(), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("revision", sa.BigInteger(), nullable=False),
    sa.Column("owner_user_id", sa.String(255), nullable=True),
    sa.Column("team_id", sa.String(255), nullable=True),
    sa.Column(
        "scope_key",
        sa.String(261),
        sa.Computed(
            "CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END",
            persisted=True,
        ),
        nullable=False,
    ),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
    sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.CheckConstraint(
        "(owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)",
        name="ck_execution_usage_facts_owner_scope",
    ),
    sa.CheckConstraint("schema_version > 0", name="ck_execution_usage_facts_schema_version"),
    sa.Index("ix_execution_usage_facts_scope_time", "scope_key", "created_at"),
    sa.ForeignKeyConstraint(
        ["run_id", "scope_key"],
        ["execution_view_runs.run_id", "execution_view_runs.scope_key"],
        name="fk_execution_usage_facts_run_scope",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint("input_tokens >= 0", name="ck_execution_usage_facts_input_tokens"),
    sa.CheckConstraint("output_tokens >= 0", name="ck_execution_usage_facts_output_tokens"),
    sa.CheckConstraint("cost_usd >= 0", name="ck_execution_usage_facts_cost_usd"),
    sa.CheckConstraint("revision >= 0", name="ck_execution_usage_facts_revision"),
    sa.UniqueConstraint("scope_key", "call_identity", name="uq_execution_usage_facts_call"),
    sa.Index("ix_execution_usage_facts_run", "run_id", "purpose", "occurred_at"),
)


class ExecutionRunViewORM(Base):
    __table__ = execution_view_runs


class ExecutionStepViewORM(Base):
    __table__ = execution_view_steps


class ExecutionPlaybackCheckpointORM(Base):
    __table__ = execution_view_checkpoints


class ExecutionViewObservationORM(Base):
    __table__ = execution_view_observations


class ArtifactVersionProvenanceORM(Base):
    __table__ = artifact_version_provenance


class ExecutionUsageFactORM(Base):
    __table__ = execution_usage_facts
