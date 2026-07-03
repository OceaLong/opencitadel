"""add partial unique index for global mcp server names

Revision ID: dd02mcpglobalname
Revises: dd01configmgmt
Create Date: 2026-07-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "dd02mcpglobalname"
down_revision: Union[str, None] = "dd01configmgmt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_mcp_servers_global_name",
        "mcp_servers",
        ["name"],
        unique=True,
        postgresql_where=sa.text("visibility = 'global'"),
    )


def downgrade() -> None:
    op.drop_index("uq_mcp_servers_global_name", table_name="mcp_servers")
