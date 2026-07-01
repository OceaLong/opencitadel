"""add llm token cache columns

Revision ID: u8v9w0x1y2z3
Revises: m3n4o5p6q7r8
Create Date: 2026-07-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u8v9w0x1y2z3"
down_revision: Union[str, None] = "m3n4o5p6q7r8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_token_usages",
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "llm_token_usages",
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "llm_token_usages",
        sa.Column("cache_metric_source", sa.String(length=64), nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    op.drop_column("llm_token_usages", "cache_metric_source")
    op.drop_column("llm_token_usages", "cache_write_tokens")
    op.drop_column("llm_token_usages", "cached_tokens")

