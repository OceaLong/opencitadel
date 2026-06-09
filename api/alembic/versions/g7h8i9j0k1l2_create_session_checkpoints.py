"""create session_checkpoints table

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "g7h8i9j0k1l2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "session_checkpoints",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(255),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("anchor_type", sa.String(32), nullable=False),
        sa.Column("anchor_event_id", sa.String(255), nullable=False),
        sa.Column("label", sa.Text(), nullable=False, server_default=""),
        sa.Column("seq_before", sa.BigInteger(), nullable=True),
        sa.Column("memories_snapshot", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("files_snapshot", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("session_state", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("sandbox_snapshot_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(0)"),
        ),
    )
    op.create_index(
        "ix_session_checkpoints_session_created",
        "session_checkpoints",
        ["session_id", "created_at"],
    )
    op.create_index(
        "ix_session_checkpoints_anchor_event",
        "session_checkpoints",
        ["session_id", "anchor_event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_session_checkpoints_anchor_event", table_name="session_checkpoints")
    op.drop_index("ix_session_checkpoints_session_created", table_name="session_checkpoints")
    op.drop_table("session_checkpoints")
