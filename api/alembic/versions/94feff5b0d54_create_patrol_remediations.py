"""Create patrol_remediations table.

Revision ID: 94feff5b0d54
Revises: f1a2b3c4d5e6
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.infrastructure.security.tenant_rls import child_predicate, disable_policy_statements, policy_statements


revision: str = "94feff5b0d54"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _execute(statements: list[str]) -> None:
    for statement in statements:
        op.execute(sa.text(statement))


def upgrade() -> None:
    op.create_table(
        "patrol_remediations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("pack_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("finding_id", sa.String(36), nullable=False),
        sa.Column("check_result_id", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(255), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("target_namespace", sa.String(255), nullable=False),
        sa.Column("target_workload", sa.String(255), nullable=False, server_default=""),
        sa.Column("target_kind", sa.String(64), nullable=False, server_default="Deployment"),
        sa.Column("params", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("params_hash", sa.String(64), nullable=False),
        sa.Column("impact_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("rollback_hint", sa.Text(), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("actuator_capability_hash", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="proposed"),
        sa.Column("before_observation", postgresql.JSONB(), nullable=True),
        sa.Column("after_observation", postgresql.JSONB(), nullable=True),
        sa.Column("recheck_run_id", sa.String(36), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["pack_id"], ["patrol_packs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["patrol_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["finding_id"], ["patrol_findings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["check_result_id"], ["patrol_check_results.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recheck_run_id"], ["patrol_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_patrol_remediations_idempotency_key"),
    )
    # A Finding may have at most one non-terminal (proposed/executing/executed)
    # Remediation at a time; terminal statuses (verified/failed/cancelled) are
    # excluded so a new proposal can follow a completed/abandoned one.
    op.create_index(
        "uq_patrol_remediations_active_finding",
        "patrol_remediations",
        ["finding_id"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('verified', 'failed', 'cancelled')"),
    )
    op.create_index("ix_patrol_remediations_fingerprint", "patrol_remediations", ["fingerprint"])
    op.create_index("ix_patrol_remediations_run_id", "patrol_remediations", ["run_id"])

    _execute(
        policy_statements(
            "patrol_remediations",
            inherited_predicate=child_predicate("patrol_remediations", "patrol_runs", "run_id", "id"),
        )
    )


def downgrade() -> None:
    _execute(disable_policy_statements("patrol_remediations"))
    op.drop_table("patrol_remediations")
