"""Create Ops Patrol tables.

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.infrastructure.security.tenant_rls import child_predicate, disable_policy_statements, policy_statements


revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, None] = "e9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _execute(statements: list[str]) -> None:
    for statement in statements:
        op.execute(sa.text(statement))


def upgrade() -> None:
    op.create_table(
        "patrol_packs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(255), nullable=False),
        sa.Column("team_id", sa.String(255), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("mcp_server_id", sa.String(255), nullable=False),
        sa.Column("skill_id", sa.String(255), nullable=False),
        sa.Column("scheduled_job_id", sa.String(255), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validated_version", sa.Integer(), nullable=True),
        sa.Column("validation_summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("version >= 1", name="ck_patrol_packs_version"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["mcp_server_id"], ["mcp_servers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["scheduled_job_id"], ["scheduled_jobs.id"], ondelete="SET NULL"),
    )
    op.create_index("uq_patrol_packs_personal_slug", "patrol_packs", ["owner_user_id", "slug"], unique=True, postgresql_where=sa.text("team_id IS NULL AND deleted_at IS NULL"))
    op.create_index("uq_patrol_packs_team_slug", "patrol_packs", ["team_id", "slug"], unique=True, postgresql_where=sa.text("team_id IS NOT NULL AND deleted_at IS NULL"))
    op.create_index("ix_patrol_packs_scope_status", "patrol_packs", ["team_id", "owner_user_id", "status"])

    op.create_table(
        "patrol_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("pack_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(255), nullable=True),
        sa.Column("pack_version", sa.Integer(), nullable=False),
        sa.Column("pack_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("submission_idempotency_key", sa.String(255), nullable=False, server_default=""),
        sa.Column("collector_capability_hash", sa.String(128), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("pass_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_completeness", sa.Numeric(5, 4), nullable=True),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("report_artifact_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("evidence_completeness IS NULL OR (evidence_completeness >= 0 AND evidence_completeness <= 1)", name="ck_patrol_runs_evidence_completeness"),
        sa.ForeignKeyConstraint(["pack_id"], ["patrol_packs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["report_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("session_id", name="uq_patrol_runs_session_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_patrol_runs_idempotency_key"),
    )
    op.create_index("uq_patrol_runs_active_pack", "patrol_runs", ["pack_id"], unique=True, postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.create_index("ix_patrol_runs_pack_created", "patrol_runs", ["pack_id", "created_at"])
    op.create_index("ix_patrol_runs_status_created", "patrol_runs", ["status", "created_at"])

    op.create_table(
        "patrol_check_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("check_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("observed", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("assertion_results", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["patrol_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "check_id", name="uq_patrol_check_results_run_check"),
    )
    op.create_index("ix_patrol_check_results_run_status", "patrol_check_results", ["run_id", "status"])

    op.create_table(
        "patrol_findings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("check_result_id", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decided_by", sa.String(255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("occurrence_count >= 1", name="ck_patrol_findings_occurrence_count"),
        sa.ForeignKeyConstraint(["run_id"], ["patrol_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["check_result_id"], ["patrol_check_results.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("run_id", "fingerprint", name="uq_patrol_findings_run_fingerprint"),
    )
    op.create_index("ix_patrol_findings_fingerprint_status", "patrol_findings", ["fingerprint", "status"])

    _execute(policy_statements("patrol_packs"))
    _execute(policy_statements("patrol_runs", inherited_predicate=child_predicate("patrol_runs", "patrol_packs", "pack_id", "id")))
    _execute(policy_statements("patrol_check_results", inherited_predicate=child_predicate("patrol_check_results", "patrol_runs", "run_id", "id")))
    _execute(policy_statements("patrol_findings", inherited_predicate=child_predicate("patrol_findings", "patrol_runs", "run_id", "id")))


def downgrade() -> None:
    for table in ("patrol_findings", "patrol_check_results", "patrol_runs", "patrol_packs"):
        _execute(disable_policy_statements(table))
    op.drop_table("patrol_findings")
    op.drop_table("patrol_check_results")
    op.drop_table("patrol_runs")
    op.drop_table("patrol_packs")
