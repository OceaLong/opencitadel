"""add pgvector embedding column to memory_entries

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-02 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        "ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS embedding vector(1536)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE memory_entries DROP COLUMN IF EXISTS embedding")
