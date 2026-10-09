"""Forward-owned table handles. Fixed 0005 DDL is independent of live Base metadata."""

import sqlalchemy as sa

metadata = sa.MetaData()
evaluation_datasets = sa.Table(
    "evaluation_datasets",
    metadata,
    sa.Column("scope_key", sa.String(261), primary_key=True),
    sa.Column("id", sa.UUID(), primary_key=True),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("revision", sa.Integer(), nullable=False),
)
