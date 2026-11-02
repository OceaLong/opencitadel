"""Forward-owned immutable summary recovery receipts, separate from Run events."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()
proposals = sa.Table(
    "execution_context_summary_proposals",
    metadata,
    sa.Column("scope_key", sa.String(261), primary_key=True),
    sa.Column("activity_id", sa.UUID, primary_key=True),
    sa.Column("run_id", sa.UUID, nullable=False),
    sa.Column("logical_id", sa.UUID, nullable=False),
    sa.Column("body", JSONB, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
    sa.UniqueConstraint("scope_key", "logical_id", name="uq_context_summary_logical"),
)
