"""Forward-owned score metadata, deliberately outside frozen initial Base."""

import sqlalchemy as sa

metadata = sa.MetaData()
evaluation_scores = sa.Table(
    "evaluation_scores",
    metadata,
    sa.Column("scope_key", sa.String(261), primary_key=True),
    sa.Column("id", sa.UUID(), primary_key=True),
)
