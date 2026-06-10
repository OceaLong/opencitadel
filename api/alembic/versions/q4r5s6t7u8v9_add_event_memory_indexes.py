"""add session_events stream_id and memory_entries scope indexes

Revision ID: q4r5s6t7u8v9
Revises: p3q4r5s6t7u8
Create Date: 2026-06-10

"""
from typing import Sequence, Union

from alembic import op

revision: str = "q4r5s6t7u8v9"
down_revision: Union[str, None] = "p3q4r5s6t7u8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_session_events_session_stream_id",
        "session_events",
        ["session_id", "stream_id"],
    )
    op.create_index(
        "ix_memory_entries_scope_last_used",
        "memory_entries",
        ["scope", "last_used_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_memory_entries_scope_last_used", table_name="memory_entries")
    op.drop_index("ix_session_events_session_stream_id", table_name="session_events")
