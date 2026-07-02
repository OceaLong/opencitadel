"""add operator_scope and browser_snapshot_key

Revision ID: z4a5b6c7d8e9
Revises: y3z4a5b6c7d8
Create Date: 2026-07-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "z4a5b6c7d8e9"
down_revision: Union[str, None] = "y3z4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("operator_scope", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "session_checkpoints",
        sa.Column("browser_snapshot_key", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("session_checkpoints", "browser_snapshot_key")
    op.drop_column("sessions", "operator_scope")
