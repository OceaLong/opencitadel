"""Forward-owned table handles, outside the frozen initial Base metadata."""

import sqlalchemy as sa

metadata = sa.MetaData()
evaluation_batches = sa.Table(
    "evaluation_batches",
    metadata,
    sa.Column("scope_key", sa.String(261), primary_key=True),
    sa.Column("id", sa.UUID(), primary_key=True),
)
