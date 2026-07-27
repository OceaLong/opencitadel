"""add scoped model preferences

Revision ID: jj05modelprefs
Revises: ii04tenantrls
Create Date: 2026-07-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.infrastructure.security.tenant_rls import (
    disable_policy_statements,
    policy_statements,
    preference_select_predicate,
    preference_write_predicate,
)

revision: str = "jj05modelprefs"
down_revision: Union[str, None] = "ii04tenantrls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_model_preferences",
        sa.Column("id", sa.String(512), nullable=False),
        sa.Column("scope_type", sa.String(32), nullable=False),
        sa.Column("owner_user_id", sa.String(255), nullable=True),
        sa.Column("team_id", sa.String(255), nullable=True),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(0)"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(0)"),
        ),
        sa.CheckConstraint(
            """
            (scope_type = 'global' AND owner_user_id IS NULL AND team_id IS NULL)
            OR (scope_type = 'user' AND owner_user_id IS NOT NULL AND team_id IS NULL)
            OR (scope_type = 'team' AND owner_user_id IS NULL AND team_id IS NOT NULL)
            """,
            name="ck_llm_model_preferences_scope_owner",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["llm_models.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_llm_model_preferences_owner_user_id",
        "llm_model_preferences",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_llm_model_preferences_team_id",
        "llm_model_preferences",
        ["team_id"],
    )
    op.create_index(
        "ix_llm_model_preferences_model_id",
        "llm_model_preferences",
        ["model_id"],
    )
    op.execute(
        """
        INSERT INTO llm_model_preferences
            (id, scope_type, owner_user_id, team_id, model_id)
        SELECT 'global', 'global', NULL, NULL, id
        FROM llm_models
        WHERE visibility = 'global'
        ORDER BY is_default DESC, created_at ASC
        LIMIT 1
        """
    )
    op.execute("UPDATE llm_models SET is_default = false")
    for statement in policy_statements(
        "llm_model_preferences",
        select_predicate=preference_select_predicate(),
        write_predicate=preference_write_predicate(),
    ):
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        """
        UPDATE llm_models
        SET is_default = true
        WHERE id = (
            SELECT model_id
            FROM llm_model_preferences
            WHERE id = 'global'
        )
        """
    )
    for statement in disable_policy_statements("llm_model_preferences"):
        op.execute(statement)
    op.drop_index(
        "ix_llm_model_preferences_model_id",
        table_name="llm_model_preferences",
    )
    op.drop_index(
        "ix_llm_model_preferences_team_id",
        table_name="llm_model_preferences",
    )
    op.drop_index(
        "ix_llm_model_preferences_owner_user_id",
        table_name="llm_model_preferences",
    )
    op.drop_table("llm_model_preferences")
