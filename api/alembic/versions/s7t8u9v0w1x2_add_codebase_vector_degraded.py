"""add codebases.vector_degraded

Revision ID: s7t8u9v0w1x2
Revises: r5s6t7u8v9w0
Create Date: 2026-06-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "s7t8u9v0w1x2"
down_revision: Union[str, None] = "r5s6t7u8v9w0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "codebases",
        sa.Column("vector_degraded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("codebases", "vector_degraded")
