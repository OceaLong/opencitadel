"""Add last_run_error to scheduled_jobs

Revision ID: cc02joberr
Revises: aa01audit
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa

revision = "cc02joberr"
down_revision = "aa01audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scheduled_jobs", sa.Column("last_run_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("scheduled_jobs", "last_run_error")
