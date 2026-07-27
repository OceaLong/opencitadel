"""add team ownership to llm endpoints and models

Revision ID: gg02llmteams
Revises: ff01llmendpoints
Create Date: 2026-07-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "gg02llmteams"
down_revision: Union[str, None] = "ff01llmendpoints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_team_id(table_name: str) -> None:
    op.add_column(table_name, sa.Column("team_id", sa.String(255), nullable=True))
    op.create_foreign_key(
        f"fk_{table_name}_team_id",
        table_name,
        "teams",
        ["team_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(f"ix_{table_name}_team_id", table_name, ["team_id"])


def _drop_team_id(table_name: str) -> None:
    op.drop_index(f"ix_{table_name}_team_id", table_name=table_name)
    op.drop_constraint(f"fk_{table_name}_team_id", table_name, type_="foreignkey")
    op.drop_column(table_name, "team_id")


def upgrade() -> None:
    _add_team_id("llm_endpoints")
    _add_team_id("llm_models")


def downgrade() -> None:
    _drop_team_id("llm_models")
    _drop_team_id("llm_endpoints")
