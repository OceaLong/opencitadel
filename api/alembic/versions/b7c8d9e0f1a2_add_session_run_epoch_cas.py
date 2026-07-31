"""add session run epoch compare-and-set state

Revision ID: b7c8d9e0f1a2
Revises: b6c7d8e9f0a1
Create Date: 2026-07-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "b6c7d8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("current_run_epoch_id", sa.String(length=511), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("current_run_epoch_seq", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "current_run_terminal_status",
            sa.String(length=32),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "current_run_terminal_status")
    op.drop_column("sessions", "current_run_epoch_seq")
    op.drop_column("sessions", "current_run_epoch_id")
