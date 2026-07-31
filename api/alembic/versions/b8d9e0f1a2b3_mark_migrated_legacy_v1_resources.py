"""mark ready pre-version resources as migrated legacy v1

Revision ID: b8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-07-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8d9e0f1a2b3"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("codebases", "knowledge_bases"):
        op.add_column(
            table,
            sa.Column(
                "legacy_v1_migrated",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        # This runs exactly at the compatibility migration boundary. Later
        # ready resources retain the false default and cannot synthesize v1.
        op.execute(
            sa.text(
                f"UPDATE {table} SET legacy_v1_migrated = true "
                "WHERE status = 'ready'"
            )
        )


def downgrade() -> None:
    op.drop_column("knowledge_bases", "legacy_v1_migrated")
    op.drop_column("codebases", "legacy_v1_migrated")
