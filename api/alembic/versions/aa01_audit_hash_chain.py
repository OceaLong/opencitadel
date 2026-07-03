"""add audit hash chain columns to audit_logs

Revision ID: aa01audit
Revises: b2c3d4e5f6g8
Create Date: 2026-07-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "aa01audit"
down_revision: Union[str, None] = "b2c3d4e5f6g8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("chain_seq", sa.BigInteger(), nullable=True))
    op.add_column(
        "audit_logs",
        sa.Column("prev_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("entry_hash", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_audit_logs_chain_seq",
        "audit_logs",
        ["chain_seq"],
        unique=True,
        postgresql_where=sa.text("chain_seq IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_chain_seq", table_name="audit_logs")
    op.drop_column("audit_logs", "entry_hash")
    op.drop_column("audit_logs", "prev_hash")
    op.drop_column("audit_logs", "chain_seq")
