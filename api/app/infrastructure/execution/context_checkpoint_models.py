"""Forward-owned checkpoint copies and a scoped CAS selection head."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()
checkpoints = sa.Table(
    "execution_context_checkpoints",
    metadata,
    sa.Column("scope_key", sa.String(261), primary_key=True),
    sa.Column("request_id", sa.UUID, primary_key=True),
    sa.Column("collection_id", sa.String(255), nullable=False),
    sa.Column("expected_revision", sa.BigInteger, nullable=False),
    sa.Column("digest", sa.String(64), nullable=False),
    sa.Column("body", JSONB, nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.clock_timestamp(),
        nullable=False,
    ),
)
heads = sa.Table(
    "execution_context_checkpoint_heads",
    metadata,
    sa.Column("scope_key", sa.String(261), primary_key=True),
    sa.Column("collection_id", sa.String(255), primary_key=True),
    sa.Column("request_id", sa.UUID, nullable=False),
    sa.Column("revision", sa.BigInteger, nullable=False),
    sa.Column("through_position", sa.BigInteger, nullable=False),
    sa.ForeignKeyConstraint(
        ["scope_key", "request_id"],
        [checkpoints.c.scope_key, checkpoints.c.request_id],
        ondelete="RESTRICT",
    ),
)
