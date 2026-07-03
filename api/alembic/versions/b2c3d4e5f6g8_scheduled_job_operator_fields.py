"""add operator fields to scheduled_jobs

Revision ID: b2c3d4e5f6g8
Revises: a1b2c3d4e5f7
Create Date: 2026-07-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "b2c3d4e5f6g8"
down_revision: Union[str, None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scheduled_jobs", sa.Column("operator_scope", sa.String(length=32), nullable=True))
    op.add_column(
        "scheduled_jobs",
        sa.Column("operator_domains", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("scheduled_jobs", sa.Column("gate_profile", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("scheduled_jobs", "gate_profile")
    op.drop_column("scheduled_jobs", "operator_domains")
    op.drop_column("scheduled_jobs", "operator_scope")
