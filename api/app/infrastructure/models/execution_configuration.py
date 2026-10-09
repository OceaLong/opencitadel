"""Forward-migration-owned accounting table handles.

Keep these out of Base.metadata: the frozen greenfield migration creates that
live metadata before running forward shape validators. F07 owns its fixed DDL.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()
execution_configurations = sa.Table(
    "execution_configurations",
    metadata,
    sa.Column("scope_key", sa.String(261), primary_key=True),
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("run_id", sa.UUID(), nullable=False),
    sa.Column("body", JSONB(), nullable=False),
    sa.Column("purpose", sa.String(32), nullable=False),
)
execution_model_dispatches = sa.Table(
    "execution_model_dispatches",
    metadata,
    sa.Column("scope_key", sa.String(261), primary_key=True),
    sa.Column("call_identity", sa.String(255), primary_key=True),
    sa.Column("run_id", sa.UUID(), nullable=False),
    sa.Column("activity_id", sa.UUID(), nullable=False),
    sa.Column("attempt_id", sa.String(255), nullable=False),
    sa.Column("configuration_id", sa.String(64), nullable=False),
    sa.Column("request_snapshot", JSONB(), nullable=False),
)
execution_model_settlements = sa.Table(
    "execution_model_settlements",
    metadata,
    sa.Column("scope_key", sa.String(261), primary_key=True),
    sa.Column("call_identity", sa.String(255), primary_key=True),
    sa.Column("fact", JSONB(), nullable=False),
)
