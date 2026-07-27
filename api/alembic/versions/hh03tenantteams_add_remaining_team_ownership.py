"""add team ownership to remaining workspace resources

Revision ID: hh03tenantteams
Revises: gg02llmteams
Create Date: 2026-07-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "hh03tenantteams"
down_revision: Union[str, None] = "gg02llmteams"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TEAM_OWNED_TABLES = (
    "skills",
    "mcp_servers",
    "a2a_servers",
    "scheduled_jobs",
)


def upgrade() -> None:
    for table_name in TEAM_OWNED_TABLES:
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


def downgrade() -> None:
    for table_name in reversed(TEAM_OWNED_TABLES):
        op.drop_index(f"ix_{table_name}_team_id", table_name=table_name)
        op.drop_constraint(f"fk_{table_name}_team_id", table_name, type_="foreignkey")
        op.drop_column(table_name, "team_id")
