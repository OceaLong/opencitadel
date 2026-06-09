"""add session and memory performance indexes

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-06-09 11:20:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_latest_message_at_desc "
        "ON sessions (latest_message_at DESC NULLS LAST)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_entries_scope_session_last_used "
        "ON memory_entries (scope, session_id, last_used_at DESC NULLS LAST, use_count DESC, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_entries_tags_gin "
        "ON memory_entries USING gin (tags)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_memory_entries_tags_gin")
    op.execute("DROP INDEX IF EXISTS idx_memory_entries_scope_session_last_used")
    op.execute("DROP INDEX IF EXISTS idx_sessions_latest_message_at_desc")
