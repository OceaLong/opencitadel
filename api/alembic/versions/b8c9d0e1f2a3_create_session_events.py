"""create append-only session_events table

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "session_events",
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(255), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stream_id", sa.String(255), nullable=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(0)")),
        sa.Column("source", sa.Text(), nullable=False, server_default="agent"),
    )
    op.create_index("ix_session_events_session_seq", "session_events", ["session_id", "seq"])
    op.create_index("ix_session_events_session_type", "session_events", ["session_id", "type"])
    op.execute(
        """
        INSERT INTO session_events (session_id, stream_id, type, payload, created_at, source)
        SELECT
            sessions.id,
            event_payload->>'id',
            COALESCE(event_payload->>'type', ''),
            event_payload,
            COALESCE(sessions.created_at, CURRENT_TIMESTAMP(0)),
            'legacy'
        FROM sessions
        CROSS JOIN LATERAL jsonb_array_elements(sessions.events) AS event_payload
        WHERE jsonb_typeof(sessions.events) = 'array'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_session_events_session_type", table_name="session_events")
    op.drop_index("ix_session_events_session_seq", table_name="session_events")
    op.drop_table("session_events")
