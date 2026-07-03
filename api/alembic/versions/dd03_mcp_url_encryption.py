"""add url_encryption column to mcp_servers

Revision ID: dd03mcpurlenc
Revises: dd02mcpglobalname
Create Date: 2026-07-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "dd03mcpurlenc"
down_revision: Union[str, None] = "dd02mcpglobalname"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column(
            "url_encryption",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'legacy_plaintext'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("mcp_servers", "url_encryption")
