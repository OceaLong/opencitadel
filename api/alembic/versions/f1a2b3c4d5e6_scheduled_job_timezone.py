"""Add timezone and Patrol source binding to scheduled jobs.

Revision ID: f1a2b3c4d5e6
Revises: f0a1b2c3d4e5
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scheduled_jobs", sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"))
    op.add_column("scheduled_jobs", sa.Column("source_type", sa.String(32), nullable=False, server_default="generic"))
    op.add_column("scheduled_jobs", sa.Column("source_id", sa.String(36), nullable=True))
    op.create_index("ix_scheduled_jobs_source", "scheduled_jobs", ["source_type", "source_id"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_jobs_source", table_name="scheduled_jobs")
    op.drop_column("scheduled_jobs", "source_id")
    op.drop_column("scheduled_jobs", "source_type")
    op.drop_column("scheduled_jobs", "timezone")
