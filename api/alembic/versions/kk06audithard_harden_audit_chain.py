"""harden audit key rotation and append-only storage

Revision ID: kk06audithard
Revises: jj05modelprefs
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "kk06audithard"
down_revision: Union[str, None] = "jj05modelprefs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column(
            "signing_key_id",
            sa.String(length=128),
            nullable=False,
            server_default=sa.text("'legacy'"),
        ),
    )
    # Preserve actor/team identifiers forever. SET NULL would mutate historical
    # evidence when a user or team is removed.
    op.drop_constraint(
        "audit_logs_actor_user_id_fkey",
        "audit_logs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "audit_logs_team_id_fkey",
        "audit_logs",
        type_="foreignkey",
    )
    op.execute(
        """
        CREATE FUNCTION opencitadel_prevent_audit_log_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'audit_logs is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW
        EXECUTE FUNCTION opencitadel_prevent_audit_log_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS opencitadel_prevent_audit_log_mutation()"
    )
    op.create_foreign_key(
        "audit_logs_actor_user_id_fkey",
        "audit_logs",
        "users",
        ["actor_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "audit_logs_team_id_fkey",
        "audit_logs",
        "teams",
        ["team_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_column("audit_logs", "signing_key_id")
